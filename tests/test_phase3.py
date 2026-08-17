from app.extensions import db
from app.models import (
    AuditLog,
    LeaveRequest,
    LeaveStatus,
    Shift,
    ShiftStatus,
    SwapAdminStatus,
    SwapPeerStatus,
    SwapRequest,
)

from .conftest import login
from .test_scheduling import create_api_shift, ids


def logout(client):
    client.post("/auth/logout")


def create_shift_as_admin(client, values, date_value, staff_key, type_key):
    return create_api_shift(
        client,
        shift_date=date_value,
        staff_id=values[staff_key],
        shift_type_id=values[type_key],
    ).json["id"]


def test_student_can_only_request_leave_for_own_shift(client, app):
    values = ids(app)
    login(client)
    other_shift_id = create_shift_as_admin(
        client, values, "2026-08-20", "student_two", "TEST_AM"
    )
    logout(client)
    login(client, "student-test", "StudentTest!2026")

    response = client.post(
        "/student/leave-requests",
        data={"shift_id": other_shift_id, "reason": "無法出席", "note": ""},
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(LeaveRequest)) == 0


def test_duplicate_leave_is_rejected_and_pending_can_cancel(client, app):
    values = ids(app)
    login(client)
    shift_id = create_shift_as_admin(client, values, "2026-08-20", "student_one", "TEST_AM")
    logout(client)
    login(client, "student-test", "StudentTest!2026")
    payload = {"shift_id": shift_id, "reason": "返鄉", "note": ""}
    assert client.post("/student/leave-requests", data=payload).status_code == 302
    page = client.get("/student/requests")
    assert page.status_code == 200
    assert "返鄉".encode() in page.data
    assert client.post("/student/leave-requests", data=payload).status_code == 302

    with app.app_context():
        requests = db.session.scalars(db.select(LeaveRequest)).all()
        assert len(requests) == 1
        request_id = requests[0].id

    assert client.post(f"/student/leave-requests/{request_id}/cancel").status_code == 302
    with app.app_context():
        assert db.session.get(LeaveRequest, request_id).status == LeaveStatus.CANCELLED


def test_requester_leave_and_swap_reasons_cannot_be_blank(client, app):
    values = ids(app)
    login(client)
    leave_shift_id = create_shift_as_admin(
        client, values, "2026-08-20", "student_one", "TEST_AM"
    )
    swap_shift_id = create_shift_as_admin(
        client, values, "2026-08-21", "student_one", "TEST_AM"
    )
    logout(client)
    login(client, "student-test", "StudentTest!2026")

    client.post(
        "/student/leave-requests",
        data={"shift_id": leave_shift_id, "reason": "   ", "note": ""},
    )
    client.post(
        "/student/swap-requests",
        data={
            "requester_shift_id": swap_shift_id,
            "target_staff_id": values["student_two"],
            "target_shift_id": "",
            "note": "   ",
        },
    )

    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(LeaveRequest)) == 0
        assert db.session.scalar(db.select(db.func.count()).select_from(SwapRequest)) == 0

    page = client.get("/student/requests")
    assert b'id="leaveReason" name="reason" maxlength="255" required' in page.data
    assert b'id="swapNote" name="note" maxlength="1000" rows="2" required' in page.data


def test_admin_approves_leave_and_preserves_vacancy_and_audit(client, app):
    values = ids(app)
    login(client)
    shift_id = create_shift_as_admin(client, values, "2026-08-20", "student_one", "TEST_AM")
    logout(client)
    login(client, "student-test", "StudentTest!2026")
    client.post(
        "/student/leave-requests",
        data={"shift_id": shift_id, "reason": "就醫", "note": "已附通知"},
    )
    with app.app_context():
        request_id = db.session.scalar(db.select(LeaveRequest.id))

    logout(client)
    login(client)
    review_page = client.get("/admin/requests")
    assert review_page.status_code == 200
    assert "就醫".encode() in review_page.data
    response = client.post(
        f"/admin/leave-requests/{request_id}/review",
        data={"decision": "APPROVE", "review_note": "准假"},
    )
    assert response.status_code == 302
    with app.app_context():
        leave = db.session.get(LeaveRequest, request_id)
        assert leave.status == LeaveStatus.APPROVED
        assert db.session.get(Shift, int(shift_id)).status == ShiftStatus.ON_LEAVE
        assert db.session.scalar(
            db.select(db.func.count()).select_from(AuditLog).where(AuditLog.action == "LEAVE_APPROVED")
        ) == 1

    events = client.get("/admin/api/shifts?start=2026-08-01&end=2026-09-01")
    vacancy = next(event for event in events.json if event["id"] == shift_id)
    assert vacancy["extendedProps"]["isVacancy"] is True


def test_student_cannot_use_admin_review_route(client, app):
    values = ids(app)
    login(client)
    shift_id = create_shift_as_admin(client, values, "2026-08-20", "student_one", "TEST_AM")
    logout(client)
    login(client, "student-test", "StudentTest!2026")
    client.post("/student/leave-requests", data={"shift_id": shift_id, "reason": "請假"})
    with app.app_context():
        request_id = db.session.scalar(db.select(LeaveRequest.id))
    assert client.post(
        f"/admin/leave-requests/{request_id}/review", data={"decision": "APPROVE"}
    ).status_code == 403


def test_swap_requester_must_own_requester_shift(client, app):
    values = ids(app)
    login(client)
    other_shift_id = create_shift_as_admin(
        client, values, "2026-08-20", "student_two", "TEST_AM"
    )
    logout(client)
    login(client, "student-test", "StudentTest!2026")
    response = client.post(
        "/student/swap-requests",
        data={
            "requester_shift_id": other_shift_id,
            "target_staff_id": values["student_two"],
            "target_shift_id": "",
            "note": "",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(SwapRequest)) == 0


def test_admin_cannot_approve_before_peer_accepts(client, app):
    values = ids(app)
    login(client)
    requester_shift_id = create_shift_as_admin(
        client, values, "2026-08-20", "student_one", "TEST_AM"
    )
    logout(client)
    login(client, "student-test", "StudentTest!2026")
    client.post(
        "/student/swap-requests",
        data={
            "requester_shift_id": requester_shift_id,
            "target_staff_id": values["student_two"],
            "target_shift_id": "",
            "note": "請對方承接",
        },
    )
    with app.app_context():
        swap_id = db.session.scalar(db.select(SwapRequest.id))
    logout(client)
    login(client)
    client.post(f"/admin/swap-requests/{swap_id}/review", data={"decision": "APPROVE"})
    with app.app_context():
        swap = db.session.get(SwapRequest, swap_id)
        assert swap.peer_status == SwapPeerStatus.PENDING
        assert swap.admin_status == SwapAdminStatus.NOT_READY
        assert db.session.get(Shift, int(requester_shift_id)).staff_id == values["student_one"]


def test_swap_creation_rejects_conflicts_before_peer_or_admin_review(client, app):
    values = ids(app)
    login(client)
    requester_shift_id = create_shift_as_admin(
        client, values, "2026-08-20", "student_one", "TEST_AM"
    )
    create_shift_as_admin(client, values, "2026-08-20", "student_two", "TEST_OVERLAP")
    logout(client)
    login(client, "student-test", "StudentTest!2026")
    client.post(
        "/student/swap-requests",
        data={
            "requester_shift_id": requester_shift_id,
            "target_staff_id": values["student_two"],
            "note": "申請換班",
        },
    )
    with app.app_context():
        swap_id = db.session.scalar(db.select(SwapRequest.id))
        assert swap_id is None
        assert db.session.get(Shift, int(requester_shift_id)).staff_id == values["student_one"]


def test_approved_swap_updates_both_shifts_and_audit(client, app):
    values = ids(app)
    login(client)
    requester_shift_id = create_shift_as_admin(
        client, values, "2026-08-20", "student_one", "TEST_AM"
    )
    target_shift_id = create_shift_as_admin(
        client, values, "2026-08-21", "student_two", "TEST_PM"
    )
    logout(client)
    login(client, "student-test", "StudentTest!2026")
    client.post(
        "/student/swap-requests",
        data={
            "requester_shift_id": requester_shift_id,
            "target_staff_id": values["student_two"],
            "target_shift_id": target_shift_id,
            "note": "互換兩天",
        },
    )
    with app.app_context():
        swap_id = db.session.scalar(db.select(SwapRequest.id))
    logout(client)
    login(client, "student-two", "StudentTwo!2026")
    client.post(f"/student/swap-requests/{swap_id}/respond", data={"decision": "ACCEPT"})
    with app.app_context():
        assert db.session.get(SwapRequest, swap_id).peer_status == SwapPeerStatus.ACCEPTED
    logout(client)
    login(client)
    response = client.post(
        f"/admin/swap-requests/{swap_id}/review",
        data={"decision": "APPROVE", "review_note": "衝突檢查通過"},
    )
    assert response.status_code == 302

    with app.app_context():
        swap = db.session.get(SwapRequest, swap_id)
        assert swap.admin_status == SwapAdminStatus.APPROVED
        assert db.session.get(Shift, int(requester_shift_id)).staff_id == values["student_two"]
        assert db.session.get(Shift, int(target_shift_id)).staff_id == values["student_one"]
        assert db.session.scalar(
            db.select(db.func.count()).select_from(AuditLog).where(AuditLog.action == "SWAP_APPROVED")
        ) == 1


def test_student_and_admin_can_filter_leave_and_swap_by_shift_month(client, app):
    values = ids(app)
    login(client)
    august_shift = create_shift_as_admin(client, values, "2026-08-24", "student_one", "TEST_AM")
    september_shift = create_shift_as_admin(client, values, "2026-09-24", "student_one", "TEST_AM")
    logout(client)
    login(client, "student-test", "StudentTest!2026")
    client.post("/student/leave-requests", data={"shift_id": august_shift, "reason": "八月請假"})
    client.post("/student/leave-requests", data={"shift_id": september_shift, "reason": "九月請假"})
    client.post(
        "/student/swap-requests",
        data={"requester_shift_id": august_shift, "target_staff_id": values["student_two"], "note": "八月換班"},
    )
    client.post(
        "/student/swap-requests",
        data={"requester_shift_id": september_shift, "target_staff_id": values["student_two"], "note": "九月換班"},
    )

    august_page = client.get("/student/requests?scope=MONTH&month=2026-08")
    assert "八月請假".encode() in august_page.data
    assert "九月請假".encode() not in august_page.data
    assert "八月換班".encode() in august_page.data
    assert "九月換班".encode() not in august_page.data

    logout(client)
    login(client)
    september_page = client.get("/admin/requests?scope=MONTH&month=2026-09")
    assert "九月請假".encode() in september_page.data
    assert "八月請假".encode() not in september_page.data
    assert "九月換班".encode() in september_page.data
    assert "八月換班".encode() not in september_page.data


def test_calendar_marks_leave_swap_and_direct_invitation(client, app):
    values = ids(app)
    login(client)
    leave_shift = create_shift_as_admin(client, values, "2026-08-25", "student_one", "TEST_AM")
    swap_shift = create_shift_as_admin(client, values, "2026-08-26", "student_one", "TEST_AM")
    logout(client)
    login(client, "student-test", "StudentTest!2026")
    client.post("/student/leave-requests", data={"shift_id": leave_shift, "reason": "月曆標示"})
    client.post(
        "/student/swap-requests",
        data={"requester_shift_id": swap_shift, "target_staff_id": values["student_two"], "note": "直接承接"},
    )

    student_events = client.get("/student/api/shifts?start=2026-08-01&end=2026-09-01").json
    leave_event = next(item for item in student_events if item["id"] == leave_shift)
    swap_event = next(item for item in student_events if item["id"] == swap_shift)
    assert leave_event["extendedProps"]["workflowAnnotations"][0]["label"] == "請假待審核"
    assert swap_event["extendedProps"]["workflowAnnotations"][0]["kind"] == "SWAP"

    logout(client)
    login(client, "student-two", "StudentTwo!2026")
    target_events = client.get("/student/api/shifts?start=2026-08-01&end=2026-09-01").json
    invitation = next(item for item in target_events if item["id"].startswith("swap-invitation-"))
    assert invitation["extendedProps"]["isSwapInvitation"] is True
    assert invitation["extendedProps"]["workflowAnnotations"][0]["label"] == "換班邀請待你回覆"

    logout(client)
    login(client)
    admin_events = client.get("/admin/api/shifts?start=2026-08-01&end=2026-09-01").json
    assert next(item for item in admin_events if item["id"] == leave_shift)["extendedProps"]["workflowAnnotations"]
