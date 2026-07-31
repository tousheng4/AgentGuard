def endpoint_base_url(endpoint: str) -> str:
    value = endpoint.rstrip("/")
    if value.startswith(("http://", "https://")):
        return f"{value}/"
    return f"http://{value}/"


def endpoint_url(endpoint: str, path: str) -> str:
    return f"{endpoint_base_url(endpoint)}{path.lstrip('/')}"
