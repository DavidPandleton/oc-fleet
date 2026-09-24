"""oc-fleet core library.

A fleet manager for OpenCode agents over HTTP API.
"""

import base64
import json
import time
import urllib.error
import urllib.request

import endpoint
from ratelimit import RetryAfter


class Fleet:
    """Client for the OpenCode HTTP API.

    Handles session dispatch, status polling, session listing and stats,
    alongside a utility to sanitize agent output.
    """

    def __init__(self, base_url=None, password_file=None, discover_endpoint=None,
                 rate_limiter=None, concurrency_limiter=None):
        """Buat klien.

        Kalau `base_url` tidak diberikan, alamat dan password DITEMUKAN
        otomatis lewat `endpoint.discover()`: perintah `opencode serve
        --service` mengikat ke port acak dan tidak menuliskannya ke file
        mana pun, jadi default tetap `http://127.0.0.1:4096` salah hampir
        selalu. Itu dulu membuat setiap panggilan gagal dengan 401 sampai
        seseorang mengoper `--base-url` dengan tangan.

        `rate_limiter` dan `concurrency_limiter` membatasi panggilan ke
        server. Provider `cutad` membatasi 25 request/menit dan 15 request
        bersamaan; tanpa pembatas, polling oc-fleet sendiri melanggar
        keduanya (terukur: 81 respons 429 di log server). Keduanya
        opsional supaya pemanggil lama dan unit test tidak terpengaruh.

        `discover_endpoint` hanya untuk pengujian: menggantikan fungsi
        penemuan supaya unit test tidak menyentuh jaringan.
        """
        penemu = discover_endpoint or endpoint.discover
        hasil = penemu(base_url=base_url, password_file=password_file)
        self.base_url = hasil["base_url"].rstrip("/")
        self.endpoint_source = hasil.get("source")
        self.endpoint_verified = hasil.get("verified", False)
        self.headers = {"Content-Type": "application/json"}
        password = hasil.get("password")
        self.password_source = hasil.get("pw_source")
        if password:
            self.headers["Authorization"] = (
                "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()
            )
        self.rate_limiter = rate_limiter
        self.concurrency_limiter = concurrency_limiter
        # Berapa kali `_request` melihat HTTP 429, dan berapa lama total
        # menunggu karena pembatas. Berguna untuk melaporkan bahwa
        # kegagalan berasal dari batas, bukan dari model.
        self.throttled_count = 0

    @staticmethod
    def sanitize(text):
        """Replace em-dash and en-dash so they are never emitted.

        Agent output reaches humans through several paths - `oc-fleet show`,
        the result JSON written by `oc-fleet-wait.py`, and the orchestrator's
        `last_text`. Only `show` used to call this, so an em-dash typed by an
        agent leaked into the other two. Rather than remember to call it at
        each call site, `status()` now sanitises as it reads, so every path
        downstream inherits it.
        """
        return text.replace("\u2014", "-").replace("\u2013", "-")

    def _request(self, method, path, data=None, _attempts=4):
        """Satu panggilan HTTP, tunduk pada pembatas laju dan konkurensi.

        Pembatas dipakai di sini - satu tempat - supaya setiap jalur
        (dispatch, status, list, cancel) ikut terbatas tanpa perlu ingat
        memanggilnya masing-masing.

        HTTP 429 dicoba ulang dengan mundur eksponensial, bukan langsung
        dilempar. Provider ini menjawab 429 saat konkurensi lewat, dan itu
        bersifat sementara: melempar langsung membuat satu spike mengubah
        menjadi kegagalan task yang tampak seperti masalah lain.
        """
        if self.rate_limiter is not None:
            self.rate_limiter.acquire()
        url = self.base_url + path
        payload = None
        if data is not None:
            payload = json.dumps(data).encode("utf-8")

        percobaan = 0
        sementara = None
        if self.concurrency_limiter is not None:
            self.concurrency_limiter.acquire()
        try:
            while True:
                percobaan += 1
                request = urllib.request.Request(
                    url, data=payload, headers=self.headers, method=method
                )
                try:
                    with urllib.request.urlopen(request) as response:
                        body = response.read()
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code != 429 or percobaan >= _attempts:
                        raise
                    self.throttled_count += 1
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    jeda = RetryAfter().delay(percobaan, retry_after)
                    sementara = jeda
                    time.sleep(jeda)
        finally:
            if self.concurrency_limiter is not None:
                self.concurrency_limiter.release()

        if sementara is not None:
            # Catat supaya pemanggil bisa membedakan "kena batas" dari
            # "modelnya rusak" - keduanya tampak sama dari outcome saja.
            self.last_throttle_wait = sementara
        if not body:
            return None
        return json.loads(body)

    @staticmethod
    def _unwrap(payload):
        """Unwrap a {'data': ...} response envelope when present, else return as-is."""
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    @staticmethod
    def _parse_model(model):
        """Parse "provider/model-id" into a body fragment, or None to omit.

        Returns None when `model` is empty (use the server default). A
        non-empty `model` must be a well formed "provider/model-id": both
        halves non-empty, surrounding whitespace trimmed. Anything else is a
        typo, and it raises rather than being sent or silently dropped.

        Why raising instead of dropping: the server accepts a malformed
        model - `POST /api/session` with `providerID: ""` answers 200 and
        stores it (verified against a live server). The session is then
        created with a provider that cannot resolve, and it only fails much
        later, where nobody connects it back to the typo. Refusing here is
        what keeps a bad model from becoming a bad session.
        """
        model = model.strip()
        if not model:
            return None
        provider, sep, model_id = model.partition("/")
        provider = provider.strip()
        model_id = model_id.strip()
        if not sep:
            raise ValueError(
                f"model must be 'provider/model-id', got {model!r} (no '/' separator)"
            )
        if not provider:
            raise ValueError(f"model is missing a provider: {model!r}")
        if not model_id:
            raise ValueError(f"model is missing a model id: {model!r}")
        return {"providerID": provider, "id": model_id}

    def dispatch(self, task, workdir, title="", model=""):
        """Create a session, post the task prompt, and return its session id.

        Both arguments are validated before any HTTP call, because the
        server accepts a bad value and only fails much later:

        * an empty ``workdir`` creates a session rooted at the wrong place,
          and the first file the agent writes lands somewhere unintended;
        * a response without an ``id`` used to surface as a bare
          ``KeyError: 'id'``, which names neither the call nor the shape
          that came back.

        Both raise here instead, so the failure points at the cause.
        """
        if not isinstance(task, str) or not task.strip():
            raise ValueError(f"task must be non-empty text, got {task!r}")
        if not isinstance(workdir, str) or not workdir.strip():
            raise ValueError(f"workdir must be non-empty text, got {workdir!r}")

        body = {"title": title, "location": {"directory": workdir}}
        if model:
            parsed = self._parse_model(model)
            if parsed is not None:
                body["model"] = parsed
        session = self._unwrap(self._request("POST", "/api/session", body))
        if not isinstance(session, dict) or not session.get("id"):
            raise RuntimeError(
                "POST /api/session did not return a session id; "
                f"got {type(session).__name__} {session!r}"
            )
        session_id = session["id"]
        self._request("POST", f"/api/session/{session_id}/prompt", {"text": task})
        return session_id

    # Berapa lama sebuah tool boleh berstatus "running" sebelum dianggap
    # macet. Tool yang benar-benar bekerja selesai dalam detik sampai
    # puluhan detik; 300 detik jauh di atas itu dan masih di bawah timeout
    # task yang lazim, jadi pemanggil diberi tahu jauh sebelum menyerah.
    STUCK_AFTER_SECONDS = 300.0

    @staticmethod
    def _tool_activity(messages, now_ms=None):
        """Ringkas keadaan tool call: berapa yang jalan, sejak kapan.

        Mengembalikan dict:
            tool_running   - jumlah tool berstatus "running"
            stuck_seconds  - umur tool running paling tua, atau None
            stuck          - apakah melewati STUCK_AFTER_SECONDS
        """
        if now_ms is None:
            now_ms = time.time() * 1000
        umur_tertua = None
        jumlah = 0
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "tool":
                    continue
                state = item.get("state")
                status = state.get("status") if isinstance(state, dict) else None
                if status != "running":
                    continue
                jumlah += 1
                waktu = item.get("time")
                dibuat = waktu.get("created") if isinstance(waktu, dict) else None
                if isinstance(dibuat, (int, float)):
                    umur = (now_ms - dibuat) / 1000.0
                    if umur_tertua is None or umur > umur_tertua:
                        umur_tertua = umur
        macet = umur_tertua is not None and umur_tertua > Fleet.STUCK_AFTER_SECONDS
        return {
            "tool_running": jumlah,
            "stuck_seconds": umur_tertua,
            "stuck": macet,
        }

    def status(self, session_id):
        """Return the outcome, last assistant text, and liveness for a session.

        Selain `outcome` dan `last_assistant_text`, hasilnya memuat:

            tool_running  - jumlah tool call yang masih berstatus "running"
            stuck_seconds - umur tool running paling tua, dalam detik
            stuck         - True kalau melewati STUCK_AFTER_SECONDS

        Kenapa perlu: pada sesi nyata di mesin ini, tiga agent berhenti
        dengan tool call berstatus "running" dan `executed: false` yang
        TIDAK PERNAH selesai. `outcome` tetap None, jadi orchestrator
        menunggu sampai timeout task penuh (25 menit dalam kasus itu) tanpa
        cara membedakan "masih bekerja" dari "sudah mati". `stuck` memberi
        sinyal itu, sehingga pemanggil bisa menyerah lebih awal dan
        menyebut sebabnya.
        """
        response = self._request("GET", f"/api/session/{session_id}/message")
        messages = self._unwrap(response)
        if not isinstance(messages, list):
            messages = []
        outcome = None
        last_text = None
        for message in messages:
            # Entries are not guaranteed to be objects; a bare string or null
            # in `data` made `message.get` raise AttributeError. Skip them
            # rather than failing the whole status read.
            if not isinstance(message, dict):
                continue
            if message.get("type") == "idle" and message.get("outcome"):
                outcome = message["outcome"]
            assistant_text = self._assistant_text(message)
            if assistant_text is not None:
                # Sanitise at the boundary rather than at each call site.
                # `oc-fleet show` called Fleet.sanitize explicitly, but the
                # result JSON from `oc-fleet-wait.py` and the orchestrator's
                # `last_text` did not, so an em-dash from an agent leaked
                # into both. Doing it here covers every consumer.
                last_text = self.sanitize(assistant_text)
        hasil = {"outcome": outcome, "last_assistant_text": last_text}
        hasil.update(self._tool_activity(messages))
        # Sesi tanpa satu pun pesan belum pernah diberi prompt. Tanpa
        # penanda ini, `cmd_status` menghitungnya sebagai "aktif" hanya
        # karena `outcome` masih None - sehingga sebuah sesi kosong yang
        # dibuat dan ditinggalkan tampak seperti pekerjaan yang berjalan.
        # Terukur: enam sesi probe kosong membuat `oc-fleet status`
        # melaporkan "6 sesi aktif" padahal tidak ada apa pun berjalan.
        hasil["started"] = bool(messages)
        return hasil

    def cancel(self, session_id):
        """Stop a running session. Tri-state return.

        `POST /api/session/{id}/interrupt` sets the session's outcome to
        "interrupted" and stops the model mid-turn (verified against a live
        server: a session told to count slowly stopped after one shell call).

        Why this exists: the orchestrator previously abandoned a timed-out
        session and immediately retried. The old session kept consuming a fleet
        slot and kept writing to the shared workdir while the retry wrote to the
        same files, so a timeout could produce two concurrent writers rather
        than one retry. Cancelling first is what makes "retry" mean "retry".

        Returns:
            True  - the server confirmed the interrupt
            False - nothing to stop: the session had already finished, or was
                    never started. Verified against a live server, which
                    answers 200 `{"interrupted": false}` for both.
            None  - the cancel did not reach a verdict: the session does not
                    exist (404), credentials were rejected (401), or the server
                    could not be reached. These are real failures, not "already
                    finished", and the caller should say so.

        Why tri-state: a plain bool collapsed five different outcomes into
        False. The orchestrator prints "stopped" only on truthy, so a cancel
        that failed because the server was down looked identical to one where
        the session had already finished - and the retry then ran alongside a
        session nobody managed to stop. That is the exact scenario this method
        exists to prevent.

        Never raises: a session that already finished will simply refuse the
        interrupt, and a cancel must never take the run down.
        """
        if not session_id:
            return None
        try:
            response = self._request("POST", f"/api/session/{session_id}/interrupt")
        except urllib.error.HTTPError as exc:
            # 404: no such session. 401: bad credentials. Both are failures to
            # act, and both must stay distinct from "already finished".
            if exc.code in (404, 401):
                return None
            return None
        except Exception:  # noqa: BLE001 - cancel is best-effort by design
            return None
        payload = self._unwrap(response)
        if isinstance(payload, dict):
            # The server answered. "interrupted" false means it had nothing to
            # stop, which is a real answer - not a failure.
            return bool(payload.get("interrupted"))
        # A truthy non-dict (e.g. a bare `{"data": true}`) still means the
        # server confirmed. Falsy means it answered without a verdict.
        return True if payload else None

    def list_sessions(self, limit=10):
        """Return a list of session dicts, newest first."""
        response = self._request("GET", f"/api/session?limit={limit}&order=desc")
        sessions = self._unwrap(response)
        return sessions if isinstance(sessions, list) else []

    def stats(self):
        """Return the session stats dict."""
        response = self._request("GET", "/api/experimental/session/stats")
        if isinstance(response, dict) and "data" in response:
            return response["data"]
        return response if isinstance(response, dict) else {}

    @staticmethod
    def _assistant_text(message):
        """Teks jawaban assistant dari satu entri pesan, atau None.

        BENTUK PESAN - ini yang membuat fungsi ini salah selama ini.
        Server OpenCode yang sebenarnya mengirim entri seperti:

            {"id": "msg_...", "type": "assistant", "model": {...},
             "content": [{"type": "reasoning", "text": "..."},
                         {"type": "text", "text": "jawabannya"}]}

        Versi lama menuntut `message["type"] == "message"` dan mencari
        `info.role`. Tidak satu pun ada di respons nyata: `type` bernilai
        "assistant" dan tidak ada `info`. Akibatnya SETIAP pesan ditolak
        dan `last_assistant_text` selalu None - terverifikasi pada 22 sesi
        nyata, nol yang punya teks. `oc-fleet show` selalu mencetak
        "(none)", dan result JSON dari oc-fleet-wait.py selalu kosong.

        Kenapa tidak ketahuan: seluruh tes memakai `{"type": "message",
        "info": {"role": "assistant"}}`, bentuk yang tidak pernah dikirim
        server. Tes menguji asumsi, bukan kenyataan.

        Fungsi ini menerima KEDUA bentuk supaya tidak ada yang rusak:
        `type == "assistant"` dengan content di level atas (nyata), dan
        `type == "message"` dengan `info.role` (bentuk lama).
        """
        # Setiap `.get` dijaga. Server pernah mengirim entri yang bukan
        # objek (`null`, string, angka) di dalam `data`, dan entri
        # `content` berupa string biasa. Satu `.get` tanpa penjagaan
        # melempar AttributeError keluar dari `status()`.
        if not isinstance(message, dict):
            return None

        jenis = message.get("type")
        if jenis == "message":
            # Bentuk lama: peran ada di `info.role`.
            info = message.get("info")
            role = info.get("role") if isinstance(info, dict) else None
            if role not in ("assistant", "user"):
                return None
        elif jenis in ("assistant", "user"):
            # Bentuk nyata: peran adalah `type` itu sendiri, dan `info`
            # tidak ada. Ini yang selama ini ditolak.
            pass
        else:
            # "idle", atau apa pun yang bukan pesan percakapan.
            return None

        content = message.get("content")
        if not isinstance(content, list):
            return None
        parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            # Hanya "text" yang dianggap jawaban. "reasoning" adalah
            # monolog internal model - pada satu sesi nyata isinya sampah
            # (`"Let's I T , __ |s"`) sementara jawaban sebenarnya kosong.
            # Mencampurnya membuat output tampak lebih baik daripada
            # kenyataannya.
            if part.get("type") == "text":
                text = part.get("text")
                # `text` bisa bukan string (atau hilang) pada input rusak.
                # Menggabungkan non-string melempar TypeError di join.
                if isinstance(text, str):
                    parts.append(text)
        if not parts:
            return None
        gabung = "\n".join(parts).strip()
        return gabung or None