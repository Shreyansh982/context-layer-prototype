"""Schema-driven enterprise knowledge retrieval over the mounted workbook."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.data.excel_loader import ExcelLoader
from app.models import KnowledgeRecord
from app.routing_trace import log_routing_event
from app.performance import timed


@dataclass(frozen=True)
class EnterpriseField:
    sheet_name: str
    column_name: str
    normalized_name: str


@dataclass
class KnowledgeSearchTrace:
    query_tokens: list[str]
    matched_fields: list[EnterpriseField] = field(default_factory=list)
    searched_sheets: list[str] = field(default_factory=list)
    returned_records: list[dict] = field(default_factory=list)


class KnowledgeService:
    """Provides authorized workbook rows and discovers fields from its schema."""

    def __init__(self):
        self.workbook_path = Path(__file__).resolve().parents[2] / "data" / "knowledge_base.xlsx"
        self.loader = ExcelLoader(self.workbook_path)
        self.field_index = self._build_field_index()

    def reload(self) -> None:
        """Atomically replace the in-process workbook snapshot after a sync."""
        self.loader = ExcelLoader(self.workbook_path)
        self.field_index = self._build_field_index()

    @staticmethod
    def _schema_tokens(value: str) -> list[str]:
        abbreviations = {"bal": "balance", "dept": "department", "emp": "employee"}
        return [abbreviations.get(token, token) for token in re.findall(r"[a-z0-9]+", value.lower().replace("_", " "))]

    def _build_field_index(self) -> tuple[EnterpriseField, ...]:
        return tuple(
            EnterpriseField(sheet_name, str(column), " ".join(self._schema_tokens(str(column))))
            for sheet_name, dataframe in self.loader.get_all_sheets().items()
            for column in dataframe.columns
        )

    def _tokenize(self, query: str) -> list[str]:
        tokens = re.findall(r"(?:>=|<=|>|<)\s*\d+(?:\.\d+)?|[A-Za-z0-9_]+", query.lower())
        return [
            token for token in tokens
            if len(token) >= 3 or re.fullmatch(r"e\d{4}", token)
            or re.match(r"(?:>=|<=|>|<)\s*\d", token)
        ]

    def match_fields(self, query: str) -> list[EnterpriseField]:
        """Match query phrases to workbook headers, not a hand-maintained term list."""
        # Ignore grammatical short words ("is", "my", "to") so prefix
        # matching cannot accidentally select columns such as INCIDENTS or ID.
        query_terms = [term for term in self._schema_tokens(query) if len(term) >= 3]
        matches = []
        for field in self.field_index:
            field_terms = [term for term in self._schema_tokens(field.column_name) if len(term) >= 3]
            if not field_terms:
                continue
            # Header abbreviations such as LEAVE_BAL match "leave balance" by
            # prefix while PROJECT_CODE and STATUS match exactly.
            if all(any(term == candidate or (len(candidate) >= 4 and (term.startswith(candidate) or candidate.startswith(term)))
                       for term in query_terms) for candidate in field_terms):
                matches.append(field)
        return matches

    def _numeric_match(self, token: str, value: float) -> bool:
        patterns = [
            (r">=\s*(\d+(?:\.\d+)?)", lambda x: value >= x),
            (r"<=\s*(\d+(?:\.\d+)?)", lambda x: value <= x),
            (r">\s*(\d+(?:\.\d+)?)", lambda x: value > x),
            (r"<\s*(\d+(?:\.\d+)?)", lambda x: value < x),
        ]
        for pattern, condition in patterns:
            match = re.match(pattern, token)
            if match:
                return condition(float(match.group(1)))
        return False

    def search(
        self,
        query: str,
        requesting_user_id: str,
        field_matches: list[EnterpriseField] | None = None,
        subject_is_authenticated_user: bool = True,
    ) -> list[KnowledgeRecord]:
        records, _ = self.search_with_trace(query, requesting_user_id, field_matches, subject_is_authenticated_user)
        return records

    def search_with_trace(
        self,
        query: str,
        requesting_user_id: str,
        field_matches: list[EnterpriseField] | None = None,
        subject_is_authenticated_user: bool = True,
    ) -> tuple[list[KnowledgeRecord], KnowledgeSearchTrace]:
        with timed("enterprise.lookup"):
            matched_fields = self.match_fields(query) if field_matches is None else field_matches
            trace = KnowledgeSearchTrace(query_tokens=self._tokenize(query), matched_fields=matched_fields)
            if matched_fields:
                records = self._search_matched_fields(matched_fields, requesting_user_id, subject_is_authenticated_user, trace)
            else:
                records = self._legacy_search(query, requesting_user_id, trace)
        trace.returned_records = [
            {"sheet": record.sheet_name, "fields": sorted(record.row_data.keys())}
            for record in records
        ]
        log_routing_event(
            "enterprise_retrieval",
            query_tokens=trace.query_tokens,
            matched_fields=[{"sheet": item.sheet_name, "field": item.column_name} for item in trace.matched_fields],
            searched_sheets=trace.searched_sheets,
            returned_records=trace.returned_records,
        )
        return records, trace

    def _search_matched_fields(
        self,
        field_matches: list[EnterpriseField],
        requesting_user_id: str,
        subject_is_authenticated_user: bool,
        trace: KnowledgeSearchTrace,
    ) -> list[KnowledgeRecord]:
        records: list[KnowledgeRecord] = []
        seen: set[tuple[str, str]] = set()
        for match in field_matches:
            dataframe = self.loader.get_sheet(match.sheet_name)
            if dataframe is None:
                continue
            trace.searched_sheets.append(match.sheet_name)
            # A question resolved to the authenticated employee must not be
            # answered from a similarly named operational field in a public row.
            if subject_is_authenticated_user and "EMP_ID" not in dataframe.columns:
                continue
            accessible = self._accessible_rows(dataframe, requesting_user_id)
            for _, row in accessible.iterrows():
                row_data = row.to_dict()
                identity = str(row_data.get("EMP_ID", ""))
                key = (match.sheet_name, identity)
                if key not in seen:
                    records.append(KnowledgeRecord(sheet_name=match.sheet_name, row_data=row_data))
                    seen.add(key)
        return records[:settings.TOP_K]

    def _legacy_search(self, query: str, requesting_user_id: str, trace: KnowledgeSearchTrace) -> list[KnowledgeRecord]:
        """Keep numeric, identifier, and record/profile search behavior intact."""
        tokens = self._tokenize(query)
        results = []
        for sheet_name, original_dataframe in self.loader.get_all_sheets().items():
            trace.searched_sheets.append(sheet_name)
            employee_specific = "EMP_ID" in original_dataframe.columns
            dataframe = self._accessible_rows(original_dataframe, requesting_user_id)
            sheet_score = sum(5 for token in tokens if token in sheet_name.lower())
            matched_columns = [
                column for column in dataframe.columns
                if any(token in column.lower() for token in tokens)
            ]
            sheet_score += len(matched_columns) * 3
            for _, row in dataframe.iterrows():
                row_score = sheet_score
                matched = bool(matched_columns) or (
                    employee_specific and self._requests_all_personal_records(tokens)
                ) or (employee_specific and self._requests_connected_employee_identity(tokens))
                for column in dataframe.columns:
                    value = row[column]
                    value_text = str(value).lower()
                    for token in tokens:
                        if token == value_text:
                            row_score += 10 + (3 if column in matched_columns else 0)
                            matched = True
                        elif token in value_text:
                            row_score += 5 + (3 if column in matched_columns else 0)
                            matched = True
                        elif isinstance(value, (int, float)) and self._numeric_match(token, value):
                            row_score += 8
                            matched = True
                if matched:
                    results.append((row_score, KnowledgeRecord(sheet_name=sheet_name, row_data=row.to_dict())))
        results.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in results[:settings.TOP_K]]

    @staticmethod
    def _accessible_rows(dataframe, requesting_user_id: str):
        if "EMP_ID" not in dataframe.columns:
            return dataframe
        employee_ids = dataframe["EMP_ID"].astype(str).str.strip().str.upper()
        return dataframe.loc[employee_ids == requesting_user_id.strip().upper()]

    @staticmethod
    def _requests_all_personal_records(tokens: list[str]) -> bool:
        return "record" in tokens or "records" in tokens or "profile" in tokens

    @staticmethod
    def _requests_connected_employee_identity(tokens: list[str]) -> bool:
        return "employee" in tokens and ("which" in tokens or "identity" in tokens)

    def describe_record(self, record: KnowledgeRecord) -> str:
        row = record.row_data
        employee = row.get("EMP_ID", "the employee")
        if record.sheet_name == "HR":
            return (f"Employee {employee} is in department {row['DEPT_ID']}, grade {row['GRADE']}, "
                    f"has a leave balance of {row['LEAVE_BAL']} and sick leave balance of "
                    f"{row['SICK_BAL']}, works shift {row['SHIFT']}, and reports to {row['MANAGER_ID']}.")
        if record.sheet_name == "IT":
            vpn_status = "enabled" if row["VPN"] else "disabled"
            return (f"Employee {employee} uses laptop model {row['LAPTOP_MODEL']} (device {row['DEVICE_ID']}); "
                    f"VPN access is {vpn_status}, with {row['STORAGE_GB']} GB storage.")
        if record.sheet_name == "Finance":
            return (f"Employee {employee} belongs to cost center {row['COST_CENTER']} with a travel limit of "
                    f"{row['TRAVEL_LIMIT']} and monthly budget of {row['MONTHLY_BUDGET']}.")
        details = ", ".join(f"{key.replace('_', ' ').lower()} {value}" for key, value in row.items())
        return f"{record.sheet_name} record: {details}."

    @staticmethod
    def reference_for(record: KnowledgeRecord) -> str:
        return json.dumps({"sheet": record.sheet_name, "row": record.row_data}, default=str, sort_keys=True)

    def verify_reference(self, reference: str, requesting_user_id: str) -> KnowledgeRecord | None:
        try:
            saved = json.loads(reference)
            sheet_name = saved["sheet"]
            saved_row = saved["row"]
        except (TypeError, KeyError, json.JSONDecodeError):
            return None
        dataframe = self.loader.get_sheet(sheet_name)
        if dataframe is None or "EMP_ID" not in dataframe.columns:
            return None
        accessible = self._accessible_rows(dataframe, requesting_user_id)
        employee_id = str(saved_row.get("EMP_ID", "")).upper()
        if employee_id != requesting_user_id.upper():
            return None
        matches = accessible[accessible["EMP_ID"].astype(str).str.upper() == employee_id]
        return None if matches.empty else KnowledgeRecord(sheet_name=sheet_name, row_data=matches.iloc[0].to_dict())


knowledge_service = KnowledgeService()
