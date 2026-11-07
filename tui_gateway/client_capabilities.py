"""Transport capability advertisement shared by the sidecar and canonical owner."""


def advertise(transport, params):
    from hermes_cli.process_identity import local_desktop_client
    from tui_gateway import server_requests
    from tui_gateway.contracts import registry as contracts
    # Each call updates only what it names; Desktop sends its fields separately for compat.
    if "server_requests" in params:
        server_requests.advertise(transport, bool(params.get("server_requests")))
    if "desktop_local" in params and hasattr(transport, "auth_identity"):
        transport.desktop_local = local_desktop_client(transport.auth_identity, params.get("desktop_local"))
        if transport.desktop_local:
            from tui_gateway import server
            server.begin_local_desktop_boot()
    return {"server_requests": sorted(contracts.SERVER_REQUESTS), "declines_not_shown": True}
