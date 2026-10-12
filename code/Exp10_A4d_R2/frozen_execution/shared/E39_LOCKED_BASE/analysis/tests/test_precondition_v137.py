"""kube-apiserver v1.37 precondition-failure wording (observed in the e39 probe, 2026-09-26, before the LOCK)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from e39_common import parse_precondition_message  # noqa: E402

RV_MSG = ('Operation cannot be fulfilled on Pod "probe-a4": the ResourceVersion in the precondition (1293) does not '
          'match the ResourceVersion in record (1294). The object might have been modified')
UID_MSG = ('Operation cannot be fulfilled on Pod "probe-a4": the UID in the precondition '
           '(00000000-0000-0000-0000-000000000000) does not match the UID in record '
           '(50683df6-746a-49db-a629-b3d6ce96e3c0). The object might have been deleted and then recreated')


def test_v137_resource_version():
    assert parse_precondition_message(RV_MSG) == {'kind': 'resourceVersion', 'precondition': '1293', 'current': '1294'}


def test_v137_uid():
    r = parse_precondition_message(UID_MSG)
    assert r['kind'] == 'uid' and r['precondition'].startswith('0000') and r['current'].startswith('50683df6')


def test_legacy_format_still_parsed():
    r = parse_precondition_message('Precondition failed: ResourceVersion in precondition: 7, '
                                   'ResourceVersion in object meta: 9')
    assert r == {'kind': 'resourceVersion', 'precondition': '7', 'current': '9'}


def test_unrelated_409_unparsed():
    r = parse_precondition_message('Operation cannot be fulfilled on pods "x": the object has been modified; '
                                   'please apply your changes to the latest version and try again')
    assert r['kind'] is None
