from app.services.knowledge import KnowledgeService


def test_employee_cannot_retrieve_another_employees_private_rows():
    records = KnowledgeService().search("E0001", requesting_user_id="E0002")

    private_sheets = {"HR", "IT", "Finance"}
    assert not [record for record in records if record.sheet_name in private_sheets]


def test_employee_can_retrieve_own_leave_balance_by_column_name():
    records = KnowledgeService().search("What is my leave balance?", requesting_user_id="E0004")

    hr_record = next(record for record in records if record.sheet_name == "HR")
    assert hr_record.row_data["EMP_ID"] == "E0004"
    assert hr_record.row_data["LEAVE_BAL"] == 22
    assert all(record.sheet_name == "HR" for record in records)


def test_employee_can_retrieve_all_of_their_private_records():
    records = KnowledgeService().search("Retrieve all my records", requesting_user_id="E0004")

    assert {record.sheet_name for record in records} == {"HR", "IT", "Finance"}
    assert {record.row_data["EMP_ID"] for record in records} == {"E0004"}


def test_connected_employee_identity_question_returns_own_records():
    records = KnowledgeService().search("Which employee am I?", requesting_user_id="E0004")

    assert {record.row_data["EMP_ID"] for record in records} == {"E0004"}
