"""Optional model price estimation; unknown models remain unknown."""


def estimate_cost(model, input_tokens, output_tokens, prices=None):
    price = (prices or {}).get(model)
    if not isinstance(price, dict):
        return None
    try:
        return round(
            (float(input_tokens) * float(price["input"])
             + float(output_tokens) * float(price["output"])) / 1_000_000,
            6,
        )
    except (KeyError, TypeError, ValueError):
        return None
