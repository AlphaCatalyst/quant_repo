from alphasieve.cli.registry import CommandResult, command


def _configure_serve(p):
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8720)


@command("serve", ("human", "system"), configure=_configure_serve, needs_state=False,
         help="serve the read-only web frontend (HTTP basic auth; credentials in <hot_root>/web.credentials)")
def cmd_serve(args, ctx) -> CommandResult:
    import uvicorn

    from alphasieve.web.app import create_app, ensure_credentials

    path = ensure_credentials(ctx.settings)
    uvicorn.run(create_app(ctx.settings), host=args.host, port=args.port, log_level="info", access_log=False)
    return CommandResult(data={"credentials": str(path)})
