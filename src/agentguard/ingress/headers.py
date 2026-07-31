from collections.abc import Mapping

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

SENSITIVE_REQUEST_HEADERS = {
    "authorization",
    "cookie",
    "agentguard-api-key",
    "agentguard-ingress-token",
}

FORWARDED_HEADERS = {
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-port",
    "x-forwarded-proto",
    "x-real-ip",
}


def filter_request_headers(
    headers: Mapping[str, str],
    endpoint_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    connection_tokens = {
        token.strip().lower()
        for token in headers.get("connection", "").split(",")
        if token.strip()
    }
    excluded = (
        HOP_BY_HOP_HEADERS
        | SENSITIVE_REQUEST_HEADERS
        | FORWARDED_HEADERS
        | connection_tokens
        | {"host"}
    )
    result = {
        key: value
        for key, value in headers.items()
        if key.lower() not in excluded
    }
    if endpoint_headers:
        result.update(endpoint_headers)
    return result


def filter_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    connection_tokens = {
        token.strip().lower()
        for token in headers.get("connection", "").split(",")
        if token.strip()
    }
    excluded = HOP_BY_HOP_HEADERS | connection_tokens | {"content-length"}
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in excluded
    }
