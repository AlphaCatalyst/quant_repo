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
