import multiprocessing as mp

from alphasieve.cli.registry import CommandResult, command
from alphasieve.evaluation import service

HUMAN_SYSTEM = ("human", "system")


def _configure_worker(p):
    p.add_argument("--queue", default=None, help="queue root (default: the local queue under the state directory)")
    p.add_argument("--processes", type=int, default=1)
    p.add_argument("--max-jobs", type=int, default=None)
    p.add_argument("--idle-exit", type=float, default=None, help="exit after this many idle seconds")


def _worker_main(settings, root, max_jobs, idle_exit):
    return service.run_worker(settings, root, max_jobs=max_jobs, idle_exit=idle_exit)


@command("evalsvc worker", HUMAN_SYSTEM, configure=_configure_worker, needs_state=False,
         help="run long-lived evaluation workers that keep the dev panel in memory")
def cmd_evalsvc_worker(args, ctx) -> CommandResult:
    root = args.queue or str(service.local_queue_root(ctx.settings))
    if args.processes <= 1:
        return CommandResult(data=service.run_worker(ctx.settings, root, args.max_jobs, args.idle_exit))
    with mp.get_context("spawn").Pool(args.processes) as pool:
        results = pool.starmap(_worker_main, [(ctx.settings, root, args.max_jobs, args.idle_exit)] * args.processes)
    return CommandResult(data={"workers": results, "jobs": sum(r["jobs"] for r in results)})


def _configure_bridge(p):
    p.add_argument("--remote", required=True, help="remote queue root on shared storage (taijifs)")
    p.add_argument("--idle-exit", type=float, default=None)


@command("evalsvc bridge", HUMAN_SYSTEM, configure=_configure_bridge, needs_state=False,
         help="forward local evaluation jobs to platform workers through a shared-storage queue")
def cmd_evalsvc_bridge(args, ctx) -> CommandResult:
    return CommandResult(data=service.run_bridge(service.local_queue_root(ctx.settings), args.remote,
                                                 idle_exit=args.idle_exit))


def _configure_status(p):
    p.add_argument("--queue", default=None)


@command("evalsvc status", HUMAN_SYSTEM, configure=_configure_status, needs_state=False,
         help="queue depth and live workers")
def cmd_evalsvc_status(args, ctx) -> CommandResult:
    queue = service.DirQueue(args.queue or service.local_queue_root(ctx.settings))
    return CommandResult(data={"queue": str(queue.root), "depth": queue.depth(), "workers": queue.alive_workers()})
