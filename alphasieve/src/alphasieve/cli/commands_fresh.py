from datetime import datetime

from alphasieve.cli.registry import CommandResult, command
from alphasieve.errors import AlphaSieveError, validation_error


def _register(p):
    p.add_argument('--trial', required=True)
    p.add_argument('--mode', choices=('diagnostic_shadow', 'validation'), required=True)
    p.add_argument('--start', required=True)
    p.add_argument('--reason', required=True)
    p.add_argument('--approve-policy', action='store_true')
    p.add_argument('--approve-operational-refit', action='store_true')
    p.add_argument('--eligibility-hash')
    p.add_argument('--request', required=True)
    p.add_argument('--signature')


def _prepare(p):
    p.add_argument('--trial', required=True)
    p.add_argument('--mode', choices=('diagnostic_shadow', 'validation'), required=True)
    p.add_argument('--start', required=True)
    p.add_argument('--eligibility-hash')


@command('fresh prepare', ('human',), configure=_prepare, needs_store=True)
def prepare(args, ctx):
    from alphasieve.fresh.service import config_from_trial, prepare_cohort
    config = config_from_trial(ctx.conn, ctx.settings, args.trial, args.mode, args.start)
    return CommandResult(data=prepare_cohort(ctx.conn, ctx.settings, config, args.eligibility_hash))


@command('fresh register', ('human',), configure=_register, needs_store=True)
def register(args, ctx):
    from alphasieve.fresh.service import approve_cohort, config_from_trial
    config = config_from_trial(ctx.conn, ctx.settings, args.trial, args.mode, args.start)
    return CommandResult(data=approve_cohort(ctx.conn, ctx.settings, config, args.reason,
        approve_policy=args.approve_policy, approve_operational_refit=args.approve_operational_refit,
        eligibility_hash=args.eligibility_hash, request_id=args.request, signature=args.signature))


@command('fresh list', ('human',))
def list_cohorts(args, ctx):
    from alphasieve.fresh.service import read_model
    return CommandResult(data=read_model(ctx.conn))


def _show(p):
    p.add_argument('--cohort', required=True)


@command('fresh show', ('human',), configure=_show)
def show_cohort(args, ctx):
    from alphasieve.fresh.service import read_model
    rows = [x for x in read_model(ctx.conn)['cohorts'] if x['cohort_id'] == args.cohort]
    if not rows:
        raise AlphaSieveError('NOT_FOUND', f'cohort {args.cohort} not found')
    return CommandResult(data=rows[0])


def _stop(p):
    p.add_argument('--cohort', required=True)
    p.add_argument('--reason', required=True)
    p.add_argument('--signature')


@command('fresh pause', ('human',), configure=_stop)
def pause(args, ctx):
    from alphasieve.fresh.service import pause_or_close
    pause_or_close(ctx.conn, ctx.settings, args.cohort, 'paused', args.reason, args.signature)
    return CommandResult(data={'cohort_id': args.cohort, 'status': 'paused'})


@command('fresh close', ('human',), configure=_stop)
def close(args, ctx):
    from alphasieve.fresh.service import pause_or_close
    pause_or_close(ctx.conn, ctx.settings, args.cohort, 'closed', args.reason, args.signature)
    return CommandResult(data={'cohort_id': args.cohort, 'status': 'closed'})


@command('fresh resume', ('human',), configure=_stop)
def resume(args, ctx):
    from alphasieve.fresh.service import pause_or_close
    pause_or_close(ctx.conn, ctx.settings, args.cohort, 'resumed', args.reason, args.signature)
    return CommandResult(data={'cohort_id': args.cohort, 'status': 'resumed'})


def _asof(p):
    p.add_argument('--asof')
    p.add_argument('--universe', default='csi800')


def _date(value):
    if value:
        try:
            return datetime.strptime(value, '%Y-%m-%d').date().isoformat()
        except ValueError as exc:
            raise validation_error('--asof must be YYYY-MM-DD') from exc
    return datetime.now().date().isoformat()


@command('data fresh-append', ('system',), configure=_asof, needs_store=False)
def fresh_append(args, ctx):
    from alphasieve.data.fresh import append_from_raw
    return CommandResult(data=append_from_raw(ctx.settings, ctx.conn, _date(args.asof), args.universe))


@command('fresh daily', ('system',), configure=_asof, needs_store=False)
def daily(args, ctx):
    from alphasieve.fresh.service import active_cohorts
    asof = _date(args.asof)
    cohorts = active_cohorts(ctx.conn, asof)
    if not cohorts:
        return CommandResult(data={'date': asof, 'cohorts': [], 'status': 'no_approved_cohort'})
    from alphasieve.fresh.daily import run_day
    return CommandResult(data={'date': asof,
        'cohorts': [run_day(ctx.settings, ctx.conn, dict(c), asof) for c in cohorts]})


def _book(p):
    p.add_argument('--book', required=True)


@command('paper show', ('human',), configure=_book)
def paper_show(args, ctx):
    book = ctx.conn.execute('SELECT * FROM paper_books WHERE book_id=?', (args.book,)).fetchone()
    if not book:
        raise AlphaSieveError('NOT_FOUND', f'book {args.book} not found')
    days = [dict(r) for r in ctx.conn.execute('SELECT date,status,nav,benchmark_nav,ret,benchmark_ret,metrics_json'
        ' FROM paper_days WHERE book_id=? ORDER BY date', (args.book,))]
    return CommandResult(data={'book': dict(book), 'days': days})


def _paper_approve(p):
    p.add_argument('--cohort', required=True)
    p.add_argument('--evidence-hash', required=True)
    p.add_argument('--reason', required=True)
    p.add_argument('--start', required=True)
    p.add_argument('--signature')


@command('paper approve', ('human',), configure=_paper_approve)
def paper_approve(args, ctx):
    from alphasieve.fresh.service import approve_paper

    return CommandResult(data=approve_paper(ctx.conn, ctx.settings, args.cohort,
                                           args.evidence_hash, args.reason, args.start, args.signature))
