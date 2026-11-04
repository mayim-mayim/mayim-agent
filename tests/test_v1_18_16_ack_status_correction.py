"""Bound public ACK exception and operation-specific OpenAPI success statuses."""
import json
import subprocess
import sys

import pytest

from mayim_agent.task_journal import JournalError, _contains_secret
from mayim_agent.task_transport import TaskTransport, TaskTransportError
from test_v1_18_0_task_journal import _journal, _freeze_retrieved_report, _acknowledgement


PUBLIC_COMPLETION_ID = "completion_" + "1" * 32


def test_bound_public_receipt_id_persists_verbatim_and_reopens_in_new_process(tmp_path):
    journal = _journal(tmp_path)
    report = _freeze_retrieved_report(journal)
    journal.mark_completion_dispatch_attempted()
    journal.mark_compound_completion_acked(_acknowledgement(completion_id=PUBLIC_COMPLETION_ID))
    assert journal.frozen_report_bytes() == report
    assert journal.compound_acknowledgement_facts()["completion_id"] == PUBLIC_COMPLETION_ID
    # The generic scan still rejects this shape outside the validated ACK slot.
    assert _contains_secret({"completion_id": PUBLIC_COMPLETION_ID})
    script = """
import json,sys
from mayim_agent.task_journal import TaskJournal
p=json.load(open(sys.argv[1]))['payload']
j=TaskJournal(sys.argv[1],task_id=p['task_id'],local_claim_credential_handle=p['local_claim_credential_handle'],task_type=p['task_type'],task_definition_version=p['task_definition_version'],task_definition_digest=p['task_definition_digest'])
assert j.load().state=='COMPOUND_COMPLETION_ACKED'
assert j.compound_acknowledgement_facts()['completion_id']==sys.argv[2]
"""
    subprocess.run([sys.executable, "-c", script, str(journal.path), PUBLIC_COMPLETION_ID], check=True)


@pytest.mark.parametrize("changes", [
    {"task_id": "task_wrong"}, {"submission_id": "sub_" + "2" * 32},
    {"report_sha256": "2" * 64}, {"manifest_sha256": "2" * 64},
    {"task_definition_digest": "2" * 64}, {"receipt_state": "PENDING"},
    {"schema_version": "wrong"}, {"evaluation_state": "wrong"},
    {"claim_token": "A" * 43}, {"private_key": "private-test-value"},
    {"report_id": "A" * 43}, {"report_id": PUBLIC_COMPLETION_ID},
    {"completion_id": "A" * 43}, {"completion_id": "completion_" + "G" * 32},
    {"completion_id": "CloudFront-Signature"},
])
def test_public_id_exception_preserves_secret_and_binding_rejection(tmp_path, changes):
    journal = _journal(tmp_path)
    _freeze_retrieved_report(journal)
    journal.mark_completion_dispatch_attempted()
    before = journal.path.read_bytes()
    fields = {"completion_id": PUBLIC_COMPLETION_ID, **changes}
    with pytest.raises(JournalError):
        journal.mark_compound_completion_acked(_acknowledgement(**fields))
    assert journal.path.read_bytes() == before
    assert journal.load().state == "REPORT_FROZEN"


OPERATIONS = [
    ("GET", "/api/agent/tasks", {200}),
    ("GET", "/api/agent/tasks/task_example", {200}),
    ("GET", "/api/agent/tasks/task_example/submissions/sub_11111111111111111111111111111111/status", {200}),
    ("POST", "/api/agent/tasks/task_example/claim", {200}),
    ("POST", "/api/agent/tasks/task_example/domain-observations", {200, 201}),
    ("POST", "/api/agent/tasks/task_example/completion", {200, 202}),
]


@pytest.mark.parametrize("method,path,allowed", OPERATIONS)
@pytest.mark.parametrize("status", [200, 201, 202, 204, 206, 299])
def test_success_codes_are_specific_to_the_operation(method, path, allowed, status):
    transport = TaskTransport(_resolver=lambda *_: ["93.184.216.34"],
        _exchange=lambda **_: (status, {"content-type": "application/json"}, b"{}"))
    args = dict(json_body={} if method == "POST" else None)
    if path.endswith(("/domain-observations", "/completion")):
        args["claim_token"] = "A" * 43
    if status in allowed:
        assert transport.request(method, path, **args).status_code == status
    else:
        with pytest.raises(TaskTransportError) as caught:
            transport.request(method, path, **args)
        assert caught.value.code == "TASK_RESPONSE_INVALID"


@pytest.mark.parametrize("path,status", [("domain-observations", 201), ("completion", 202)])
@pytest.mark.parametrize("body", [b"null", b"[]", b"{", b'{"a":1,"a":2}'])
def test_new_success_codes_still_reject_invalid_json_objects(path, status, body):
    transport = TaskTransport(_resolver=lambda *_: ["93.184.216.34"],
        _exchange=lambda **_: (status, {"content-type": "application/json"}, body))
    with pytest.raises(TaskTransportError) as caught:
        transport.request("POST", "/api/agent/tasks/task_example/" + path,
            claim_token="A" * 43, json_body={})
    assert caught.value.code == "TASK_RESPONSE_INVALID"
