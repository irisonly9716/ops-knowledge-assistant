"""Validating an uploaded CSV: what is accepted, and that every refusal says what to fix and where."""
from decimal import Decimal

import pytest

from app.consumers.data_tables import MAX_REPORTED_PROBLEMS, TABLES, TableValidationError, parse_table

PRODUCTS = "campaign_products"
CUSTOMERS = "campaign_customer_behavior"
CAMPAIGN = 11

PRODUCT_HEADER = ("product_name,units_sold,sell_through_rate,return_rate,sales_amount,"
                  "click_through_rate,cost_price,profit,profit_margin")
PRODUCT_ROW = "Hydrating Serum,120,0.75,0.02,3600.50,0.031,12.00,2160.50,0.60"
CUSTOMER_HEADER = "customer_ref,region,dwell_time_minutes,products_purchased,purchase_amount,ip_address"


def products(*rows: str, header: str = PRODUCT_HEADER) -> bytes:
    return "\n".join([header, *rows]).encode()


def customers(*rows: str) -> bytes:
    return "\n".join([CUSTOMER_HEADER, *rows]).encode()


def error_of(table: str, data: bytes) -> str:
    with pytest.raises(TableValidationError) as e:
        parse_table(table, data, CAMPAIGN)
    return str(e.value)


def test_a_good_products_file_gives_typed_rows_in_column_order():
    assert parse_table(PRODUCTS, products(PRODUCT_ROW), CAMPAIGN) == [(
        "Hydrating Serum", 120, Decimal("0.75"), Decimal("0.02"), Decimal("3600.50"), Decimal("0.031"),
        Decimal("12.00"), Decimal("2160.50"), Decimal("0.60"),
    )]


def test_amounts_are_decimals_so_no_binary_rounding_gets_in():
    row = parse_table(PRODUCTS, products(PRODUCT_ROW), CAMPAIGN)[0]
    assert isinstance(row[4], Decimal)
    assert str(row[4]) == "3600.50"


def test_columns_can_come_in_any_order():
    header = ("units_sold,product_name,sell_through_rate,return_rate,sales_amount,"
              "click_through_rate,cost_price,profit,profit_margin")
    row = "120,Hydrating Serum,0.75,0.02,3600.50,0.031,12.00,2160.50,0.60"
    assert parse_table(PRODUCTS, products(row, header=header), CAMPAIGN)[0][:2] == ("Hydrating Serum", 120)


def test_excels_utf8_bom_is_not_part_of_the_first_column_name():
    assert len(parse_table(PRODUCTS, b"\xef\xbb\xbf" + products(PRODUCT_ROW), CAMPAIGN)) == 1


def test_windows_line_endings_blank_lines_and_spaces_around_cells_are_fine():
    data = f"{PRODUCT_HEADER}\r\n{PRODUCT_ROW}\r\n\r\n ,,,,,,,, \r\n".encode()
    assert len(parse_table(PRODUCTS, data, CAMPAIGN)) == 1
    spaced = products(" Hydrating Serum , 120 ,0.75,0.02,3600.50,0.031,12.00,2160.50,0.60")
    assert parse_table(PRODUCTS, spaced, CAMPAIGN)[0][:2] == ("Hydrating Serum", 120)


def test_a_quoted_cell_may_contain_a_comma():
    row = '"Serum, 30ml",120,0.75,0.02,3600.50,0.031,12.00,2160.50,0.60'
    assert parse_table(PRODUCTS, products(row), CAMPAIGN)[0][0] == "Serum, 30ml"


def test_the_campaign_id_column_is_optional_and_must_match_the_upload():
    header = f"campaign_id,{PRODUCT_HEADER}"
    assert len(parse_table(PRODUCTS, products(f"{CAMPAIGN},{PRODUCT_ROW}", header=header), CAMPAIGN)) == 1

    message = error_of(PRODUCTS, products(f"12,{PRODUCT_ROW}", header=header))
    assert "row 2, campaign_id: '12' is not this upload's campaign (11)" in message


@pytest.mark.parametrize(("header", "expected"), [
    (PRODUCT_HEADER.replace(",return_rate", ""), "missing columns: return_rate"),
    (PRODUCT_HEADER + ",notes", "unknown columns: notes"),
    (PRODUCT_HEADER.replace("units_sold", "Units Sold"), "missing columns: units_sold; unknown columns: Units Sold"),
    (PRODUCT_HEADER + ",units_sold", "repeated columns: units_sold"),
])
def test_a_wrong_header_says_which_columns(header, expected):
    assert expected in error_of(PRODUCTS, products(header=header))


def test_a_wrong_header_lists_what_is_expected():
    assert PRODUCT_HEADER.replace(",", ", ") in error_of(PRODUCTS, b"name,units\nSerum,1\n")


@pytest.mark.parametrize("data", [b"", b"\n\n"])
def test_a_file_with_no_header_is_refused(data):
    assert "missing columns" in error_of(PRODUCTS, data)


def test_a_header_with_no_rows_is_refused():
    assert "no data rows" in error_of(PRODUCTS, products())


def test_a_file_in_another_encoding_is_refused():
    assert "not UTF-8" in error_of(PRODUCTS, products("Crème,1,0.1,0.1,1,0.1,1,1,0.1").decode().encode("latin-1"))


def test_nul_bytes_are_refused_here_not_in_the_database():
    assert "NUL bytes" in error_of(PRODUCTS, products(PRODUCT_ROW.replace("Hydrating", "Hydr\x00ating")))


def test_an_error_names_the_row_the_column_and_the_value():
    data = products(PRODUCT_ROW, "Lip Tint,abc,0.75,0.02,3600.50,0.031,12.00,2160.50,0.60")
    assert error_of(PRODUCTS, data) == "row 3, units_sold: 'abc' is not a whole number"


def test_all_problems_are_reported_at_once():
    data = products("A,x,0.75,0.02,3600.50,0.031,12.00,2160.50,0.60",
                    "B,1,2,0.02,3600.50,0.031,12.00,2160.50,0.60")
    message = error_of(PRODUCTS, data)
    assert "row 2, units_sold" in message
    assert "row 3, sell_through_rate" in message


def test_a_long_list_of_problems_is_cut_short_and_says_how_many_more():
    rows = [f"P{i},x,0.75,0.02,3600.50,0.031,12.00,2160.50,0.60" for i in range(MAX_REPORTED_PROBLEMS + 5)]
    message = error_of(PRODUCTS, products(*rows))
    assert message.count("is not a whole number") == MAX_REPORTED_PROBLEMS
    assert message.endswith("and 5 more problem(s)")


def test_a_huge_cell_is_not_copied_into_the_error():
    message = error_of(PRODUCTS, products(f"P,{'x' * 5000},0.75,0.02,3600.50,0.031,12.00,2160.50,0.60"))
    assert len(message) < 200


def test_a_row_with_the_wrong_number_of_cells():
    assert error_of(PRODUCTS, products("Serum,120,0.75")) == "row 2 has 3 cells, expected 9"


def test_a_required_cell_may_not_be_empty():
    message = error_of(PRODUCTS, products(",120,0.75,0.02,,0.031,12.00,2160.50,0.60"))
    assert "row 2, product_name: is empty" in message
    assert "row 2, sales_amount: is empty" in message


def test_the_same_product_twice_is_refused_because_the_table_allows_it_once():
    assert "row 3, product_name: 'Serum' appears more than once" in error_of(
        PRODUCTS, products(f"Serum,{PRODUCT_ROW.split(',', 1)[1]}", f"Serum,{PRODUCT_ROW.split(',', 1)[1]}"))


@pytest.mark.parametrize("value", ["1.5", "-1", "1,000", "12.0", "١٢", "²"])
def test_units_sold_is_a_plain_whole_number(value):
    cell = f'"{value}"' if "," in value else value
    assert "units_sold" in error_of(PRODUCTS, products(f"Serum,{cell},0.75,0.02,3600.50,0.031,12.00,2160.50,0.60"))


@pytest.mark.parametrize("value", ["abc", "NaN", "Infinity", "-Infinity", "1.0.0"])
def test_things_that_are_not_finite_numbers_are_refused(value):
    assert "sales_amount" in error_of(PRODUCTS, products(f"Serum,1,0.75,0.02,{value},0.031,12.00,2160.50,0.60"))


@pytest.mark.parametrize(("position", "column"), [(2, "sell_through_rate"), (3, "return_rate"), (5, "click_through_rate")])
@pytest.mark.parametrize("value", ["1.00001", "-0.01", "75"])
def test_a_rate_is_a_share_between_0_and_1(position, column, value):
    cells = PRODUCT_ROW.split(",")
    cells[position] = value
    assert column in error_of(PRODUCTS, products(",".join(cells)))


@pytest.mark.parametrize("value", ["0", "1", "1.0000"])
def test_the_ends_of_the_rate_range_are_accepted(value):
    cells = PRODUCT_ROW.split(",")
    cells[2] = value
    assert parse_table(PRODUCTS, products(",".join(cells)), CAMPAIGN)[0][2] == Decimal(value)


def test_a_product_sold_at_a_loss_has_negative_profit_and_margin():
    row = parse_table(PRODUCTS, products("Serum,1,0.5,0,10,0.1,15,-5,-0.5"), CAMPAIGN)[0]
    assert (row[7], row[8]) == (Decimal(-5), Decimal("-0.5"))


@pytest.mark.parametrize(("position", "column"), [(4, "sales_amount"), (6, "cost_price")])
def test_sales_and_cost_can_not_be_negative(position, column):
    cells = PRODUCT_ROW.split(",")
    cells[position] = "-1"
    assert column in error_of(PRODUCTS, products(",".join(cells)))


def test_a_good_customers_file_splits_the_products_into_a_list():
    rows = parse_table(CUSTOMERS, customers("C-1,Northeast,12.5,Serum; Lip Tint,99.90,203.0.113.7"), CAMPAIGN)
    customer_ref, region, minutes, purchased, amount, ip = rows[0]
    assert (customer_ref, region, minutes, amount, ip) == ("C-1", "Northeast", Decimal("12.5"), Decimal("99.90"),
                                                           "203.0.113.7")
    assert purchased.obj == ["Serum", "Lip Tint"]


def test_optional_customer_cells_can_be_empty_and_a_customer_can_buy_nothing():
    (_, region, _, purchased, _, ip), = parse_table(CUSTOMERS, customers("C-1,,12.5,,0,"), CAMPAIGN)
    assert region is None
    assert ip is None
    assert purchased.obj == []   # the column's default; NULL would make "how many products" unanswerable


def test_the_same_customer_may_appear_twice():
    assert len(parse_table(CUSTOMERS, customers("C-1,,1,,0,", "C-1,,2,,0,"), CAMPAIGN)) == 2


def test_an_ipv6_address_is_accepted_and_a_bad_one_is_refused():
    assert parse_table(CUSTOMERS, customers("C-1,,1,,0,2001:db8::1"), CAMPAIGN)[0][5] == "2001:db8::1"
    assert error_of(CUSTOMERS, customers("C-1,,1,,0,999.1.1.1")) == "row 2, ip_address: '999.1.1.1' is not an IP address"


def test_every_table_type_of_the_schema_has_a_spec():
    assert set(TABLES) == {PRODUCTS, CUSTOMERS}
