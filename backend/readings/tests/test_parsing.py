import io

import pandas as pd
from django.test import SimpleTestCase

from readings.parsing import (
    FileParseError,
    detect_columns,
    parse_temperature,
    parse_timestamp,
    read_rows,
    to_celsius,
)


def make_csv(headers, rows) -> io.BytesIO:
    lines = [",".join(headers)]
    lines += [",".join(str(v) for v in row) for row in rows]
    return io.BytesIO("\n".join(lines).encode("utf-8"))


def make_excel(headers, rows) -> io.BytesIO:
    df = pd.DataFrame(rows, columns=headers)
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer


class DetectColumnsTests(SimpleTestCase):
    def test_finds_canonical_columns_regardless_of_order(self):
        mapping = detect_columns(["Temp", "Logger", "Branch", "Fridge", "Time"])
        self.assertEqual(mapping["timestamp"], "Time")
        self.assertEqual(mapping["temperature"], "Temp")
        self.assertEqual(mapping["logger"], "Logger")

    def test_branch_and_fridge_columns_are_ignored(self):
        mapping = detect_columns(["Logger", "Branch", "Fridge", "Time", "Temp"])
        self.assertNotIn("branch", mapping)
        self.assertNotIn("fridge", mapping)

    def test_logger_column_is_optional(self):
        mapping = detect_columns(["Time", "Temp"])
        self.assertNotIn("logger", mapping)

    def test_missing_time_or_temp_column_raises(self):
        with self.assertRaises(FileParseError):
            detect_columns(["Logger", "Branch", "Fridge"])

    def test_lone_date_column_with_no_time_is_rejected(self):
        # Every supported format needs a time-of-day component.
        with self.assertRaises(FileParseError):
            detect_columns(["Date", "Temp"])

    def test_header_matching_is_case_and_whitespace_insensitive(self):
        mapping = detect_columns([" TIMESTAMP ", "temperature"])
        self.assertEqual(mapping["timestamp"], " TIMESTAMP ")
        self.assertEqual(mapping["temperature"], "temperature")

    def test_combined_timestamp_header_is_used_as_is(self):
        mapping = detect_columns(["Timestamp", "Temp"])
        self.assertEqual(mapping["timestamp"], "Timestamp")
        self.assertNotIn("date", mapping)
        self.assertNotIn("time", mapping)

    def test_separate_date_and_time_columns_are_both_captured(self):
        mapping = detect_columns(["Date", "Time", "Temp"])
        self.assertEqual(mapping["date"], "Date")
        self.assertEqual(mapping["time"], "Time")
        self.assertNotIn("timestamp", mapping)

    def test_lone_time_column_is_treated_as_the_combined_column(self):
        # Matches the sample data: a "Time" header whose values are already
        # full "2026-09-14 06:00" datetimes, with no separate Date column.
        mapping = detect_columns(["Time", "Temp"])
        self.assertEqual(mapping["timestamp"], "Time")
        self.assertNotIn("date", mapping)


class ParseTimestampTests(SimpleTestCase):
    def test_iso_format(self):
        dt = parse_timestamp("2026-09-14 06:00")
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour, dt.minute), (2026, 9, 14, 6, 0))

    def test_haifa_dd_mm_yyyy_format(self):
        dt = parse_timestamp("14/09/2026 06:00")
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour, dt.minute), (2026, 9, 14, 6, 0))

    def test_unparseable_timestamp_returns_none(self):
        self.assertIsNone(parse_timestamp("not a date"))

    def test_empty_timestamp_returns_none(self):
        self.assertIsNone(parse_timestamp(""))


class ParseTemperatureTests(SimpleTestCase):
    def test_parses_plain_number(self):
        self.assertEqual(parse_temperature("3.8"), 3.8)

    def test_err_returns_none(self):
        self.assertIsNone(parse_temperature("ERR"))

    def test_empty_returns_none(self):
        self.assertIsNone(parse_temperature(""))


class ToCelsiusTests(SimpleTestCase):
    def test_celsius_passthrough(self):
        self.assertEqual(to_celsius(3.8, "C"), 3.8)

    def test_fahrenheit_conversion_matches_sample_data(self):
        # The Haifa sample rows (38.3F / 39.0F) should land near other
        # branches' ordinary dairy-fridge readings once converted.
        self.assertAlmostEqual(to_celsius(38.3, "F"), 3.5, places=1)
        self.assertAlmostEqual(to_celsius(39.0, "F"), 3.89, places=1)

    def test_fahrenheit_conversion_is_rounded_cleanly(self):
        # Binary float division leaves noise like 3.4999999999999982 — round
        # it away rather than let it leak into storage/API responses.
        self.assertEqual(to_celsius(38.3, "F"), 3.5)


class ReadRowsTests(SimpleTestCase):
    def test_reads_csv_with_logger_column(self):
        csv_file = make_csv(
            ["Logger", "Branch", "Fridge", "Time", "Temp"],
            [["TL-0512", "Jerusalem", "Dairy", "2026-09-14 06:00", "3.8"]],
        )
        rows = read_rows(csv_file, "week.csv")
        self.assertEqual(
            rows[0],
            {"logger_code": "TL-0512", "timestamp_raw": "2026-09-14 06:00", "temperature_raw": "3.8"},
        )

    def test_reads_csv_with_reordered_columns(self):
        csv_file = make_csv(
            ["Time", "Temp", "Logger"],
            [["2026-09-14 06:00", "3.8", "TL-0512"]],
        )
        rows = read_rows(csv_file, "week.csv")
        self.assertEqual(rows[0]["logger_code"], "TL-0512")

    def test_reads_raw_two_column_file_with_no_logger_column(self):
        csv_file = make_csv(["Time", "Temp"], [["2026-09-14 06:00", "3.8"]])
        rows = read_rows(csv_file, "raw.csv")
        self.assertIsNone(rows[0]["logger_code"])

    def test_preserves_err_literal(self):
        csv_file = make_csv(["Time", "Temp"], [["14/09/2026 06:30", "ERR"]])
        rows = read_rows(csv_file, "haifa.csv")
        self.assertEqual(rows[0]["temperature_raw"], "ERR")

    def test_reads_excel_file(self):
        excel_file = make_excel(
            ["Logger", "Time", "Temp"], [["TL-0417", "2026-09-14 06:00", "4.1"]]
        )
        rows = read_rows(excel_file, "week.xlsx")
        self.assertEqual(rows[0]["logger_code"], "TL-0417")
        self.assertEqual(rows[0]["temperature_raw"], "4.1")

    def test_unsupported_extension_raises(self):
        with self.assertRaises(FileParseError):
            read_rows(io.BytesIO(b"whatever"), "notes.txt")

    def test_empty_csv_raises_parse_error_not_pandas_exception(self):
        # A 0-byte CSV makes pandas raise EmptyDataError — must surface as
        # our own FileParseError (and therefore a clean 400), not escape
        # as an unhandled exception.
        with self.assertRaises(FileParseError):
            read_rows(io.BytesIO(b""), "empty.csv")

    def test_empty_excel_raises_parse_error_not_pandas_exception(self):
        with self.assertRaises(FileParseError):
            read_rows(io.BytesIO(b""), "empty.xlsx")

    def test_corrupt_csv_bytes_raise_parse_error(self):
        # Garbage bytes with inconsistent row widths make pandas' C parser
        # raise ParserError rather than EmptyDataError — same contract.
        garbage = b"Time,Temp\n\x00\x01\x02,\"unterminated\n1,2,3,4,5"
        with self.assertRaises(FileParseError):
            read_rows(io.BytesIO(garbage), "corrupt.csv")

    def test_separate_date_and_time_columns_are_combined(self):
        csv_file_obj = make_csv(
            ["Date", "Time", "Temp"],
            [["2026-09-14", "06:00", "3.8"]],
        )
        rows = read_rows(csv_file_obj, "branch.csv")
        self.assertEqual(rows[0]["timestamp_raw"], "2026-09-14 06:00")

    def test_combined_timestamp_column_is_used_directly(self):
        csv_file_obj = make_csv(["Timestamp", "Temp"], [["2026-09-14 06:00", "3.8"]])
        rows = read_rows(csv_file_obj, "branch.csv")
        self.assertEqual(rows[0]["timestamp_raw"], "2026-09-14 06:00")
