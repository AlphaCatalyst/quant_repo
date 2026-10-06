"""Health alert persistence remains public and condition-keyed."""

from alphasieve.control import health
from alphasieve.monitor import list_alerts
from alphasieve.state import connect


def test_record_system_alert_dedupes(settings):
    with connect(settings.state_db) as conn:
        assert health.record_system_alert(settings, conn, rule='unit', subject='web',
                                          title='服务失败', detail='退出 1', dedupe_key='exit-1')
        assert not health.record_system_alert(settings, conn, rule='unit', subject='web',
                                              title='服务失败', detail='退出 1', dedupe_key='exit-1')
        assert health.record_system_alert(settings, conn, rule='unit', subject='web',
                                          title='服务失败', detail='退出 2', dedupe_key='exit-2')
        alerts = list_alerts(conn, visibility='public', kind='system')
        assert len(alerts) == 2
        assert all(a['visibility'] == 'public' for a in alerts)


def test_evalbridge_warns_only_when_work_waits_without_workers(settings):
    import json

    queue = settings.state_db.parent / 'evalq'
    (queue / 'workers').mkdir(parents=True)
    (queue / 'pending').mkdir()
    snap = {'local': {'units': {'alphasieve-evalbridge.service': {'state': 'active'}}}}
    assert health._evalbridge(settings, snap)['status'] == 'ok'
    (queue / 'pending' / 'J1.json').write_text('{}')
    assert health._evalbridge(settings, snap)['status'] == 'warn'
    (queue / 'workers' / 'bridge-h-1.json').write_text(json.dumps({'remote_workers': 2}))
    check = health._evalbridge(settings, snap)
    assert check['status'] == 'ok' and '远端 worker 2 个' in check['detail']
    assert health._evalbridge(settings, {'local': {'units': {}}})['status'] == 'fail'
