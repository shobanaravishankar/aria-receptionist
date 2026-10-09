"""The Services-tab reader and its scroll-until-complete collector, against a fake lazy list shaped like the one Sol observed."""

from __future__ import annotations

import pytest

from aria_booking.services_reader import ListedService, SCROLL_SERVICES_JS, collect_services, parse_services


def node(depth, testid=None, cls=(), text=None):
    return {"tag": "div", "depth": depth, "cls": list(cls), "attrs": [], "role": None, "testid": testid, "aria": None, "text": text, "box": [0, 0, 1, 1]}


def row_nodes(service_id, name, duration="30min+", price="from $49.00", depth=7):
    return [
        node(depth, f"services-list-item-{service_id}"),
        node(depth + 1, None, ["_optionSeparator_x"]),
        node(depth + 1, None, ["_optionName_x"], name),
        node(depth + 1, None, ["duration", "_optionDuration_x"], duration),
        node(depth + 1, None, ["_optionPrice_x"], price),
    ]


def page(rows, total, *, tab=True, outside=()):
    nodes = [node(3, "services", [], f"Services ({total})")] if tab else []
    nodes += [node(4, "services-option-list", ["b-inifinite-scroll", "scrollable"]), node(5, None, ["_content_x"])]
    for service_id, name in rows:
        nodes += [node(6)] + row_nodes(service_id, name)
    nodes += [node(4)] + list(outside)
    return nodes


ROWS = [(str(7000 + i), f"Service {i}") for i in range(63)]
BATCHES = [12, 27, 36, 51, 61, 63]


class LazyList:
    """Shows 12 rows, then more after each scroll, but only AFTER a lag of a few reads (the old count shows first)."""

    def __init__(self, total=63, batches=BATCHES, lag=2, stuck_at=None):
        self.total, self.batches, self.lag, self.stuck_at = total, batches, lag, stuck_at
        self.step, self.pending, self.reads, self.scrolls, self.clock = 0, None, 0, 0, 0.0

    def shown(self):
        return min(self.batches[self.step], self.stuck_at if self.stuck_at is not None else 10 ** 9)

    def capture(self):
        self.reads += 1
        if self.pending is not None:
            self.pending -= 1
            if self.pending <= 0 and self.step + 1 < len(self.batches):
                self.step += 1
                self.pending = None
        return page(ROWS[: self.shown()], self.total)

    def scroll(self):
        self.scrolls += 1
        self.pending = self.lag
        return True

    def sleep(self, seconds):
        self.clock += seconds

    def monotonic(self):
        return self.clock


def run(lazy, **kw):
    return collect_services(lazy.capture, lazy.scroll, sleep=lazy.sleep, monotonic=lazy.monotonic, **kw)


# ---------------------------------------------------------------- parsing


def test_rows_are_read_with_ids_names_and_the_parents_summary():
    result = parse_services(page([("5521", "Deep  Tissue Massage"), ("5522", "Swedish Massage")], 2))
    assert result.services[0] == ListedService("5521", "Deep Tissue Massage", "30min+", "from $49.00"), "repeated whitespace is normalised"
    assert result.expected_total == 2 and result.complete and result.ids() == frozenset({"5521", "5522"})


def test_the_total_comes_from_the_tab_label():
    for text, expected in (("Services (63)", 63), ("Services(7)", 7), ("Services  ( 12 )", None), ("Staff (5)", None)):
        nodes = [node(3, "services", [], text)] + page([], 0, tab=False)
        assert parse_services(nodes).expected_total == expected, text


def test_a_list_without_the_total_is_never_complete():
    result = parse_services(page(ROWS[:5], 5, tab=False))
    assert result.expected_total is None and not result.complete and any("total was not found" in p for p in result.problems)


def test_a_page_without_the_list_is_incomplete():
    result = parse_services([node(3, "services", [], "Services (4)")])
    assert not result.complete and any("list was not found" in p for p in result.problems) and result.services == ()


def test_fewer_rows_than_the_total_is_incomplete_and_more_is_inconsistent():
    assert not parse_services(page(ROWS[:12], 63)).complete
    over = parse_services(page(ROWS[:5], 3))
    assert not over.complete and any("disagree" in p for p in over.problems)


def test_a_repeated_row_id_is_a_problem_not_a_second_service():
    result = parse_services(page([("1", "A"), ("1", "A again"), ("2", "B")], 2))
    assert len(result.services) == 2 and not result.complete and any("more than once" in p for p in result.problems)


def test_a_row_with_no_readable_name_is_a_problem():
    nodes = page([("1", "A")], 1)
    for n in nodes:
        if "_optionName_x" in n["cls"]:
            n["text"] = "   "
    result = parse_services(nodes)
    assert not result.complete and any("no readable name" in p for p in result.problems)


def test_rows_outside_the_list_are_not_services():
    outside = row_nodes("9999", "Elsewhere on the page", depth=4)
    result = parse_services(page([("1", "A")], 1, outside=outside))
    assert result.ids() == frozenset({"1"}) and result.complete


def test_a_row_without_a_duration_or_price_is_still_a_service_with_blank_text():
    nodes = page([("1", "A")], 1)
    nodes = [n for n in nodes if not ("_optionDuration_x" in n["cls"] or "_optionPrice_x" in n["cls"])]
    (svc,) = parse_services(nodes).services
    assert (svc.duration_text, svc.price_text) == ("", "")


# ---------------------------------------------------------------- the collector


def test_a_lazy_list_is_scrolled_to_completion_and_reconciles_with_the_total():
    lazy = LazyList()
    result = run(lazy)
    assert result.complete and len(result.services) == 63 and result.ids() == {s[0] for s in ROWS}
    assert lazy.scrolls == 5 and lazy.reads > lazy.scrolls, "it looked several times after each scroll instead of trusting the first, stale read"


def test_a_scroll_whose_new_rows_arrive_late_is_waited_for():
    result = run(LazyList(lag=5), settle_seconds=3.0, poll_seconds=0.4)
    assert result.complete


def test_it_gives_up_when_the_list_stops_short_and_says_where():
    lazy = LazyList(stuck_at=36)
    result = run(lazy)
    assert not result.complete and len(result.services) == 36 and any("stopped growing at 36 of 63" in p for p in result.problems)
    assert lazy.scrolls == 2 + 3, "two scrolls that added rows, then three that added nothing"


def test_it_never_scrolls_a_list_that_is_already_complete():
    lazy = LazyList(total=12, batches=[12])
    assert run(lazy).complete and lazy.scrolls == 0


def test_a_total_that_never_appears_ends_incomplete():
    class NoTotal(LazyList):
        def capture(self):
            return [n for n in super().capture() if n.get("testid") != "services"]

    result = run(NoTotal())
    assert not result.complete and any("total was not found" in p for p in result.problems)


def test_a_list_that_cannot_be_scrolled_ends_incomplete_at_once():
    lazy = LazyList()
    lazy.scroll = lambda: False
    result = run(lazy)
    assert not result.complete and any("could not be scrolled" in p for p in result.problems) and len(result.services) == 12


def test_the_time_limit_bounds_the_run():
    result = run(LazyList(lag=50), settle_seconds=100, max_seconds=5.0, poll_seconds=0.5)
    assert not result.complete and any("gave up after 5 seconds" in p for p in result.problems)


def test_the_round_limit_bounds_the_run():
    lazy = LazyList(batches=list(range(12, 100)), total=200, lag=1)
    result = run(lazy, max_rounds=5)
    assert not result.complete and any("gave up after 5 scrolls" in p for p in result.problems) and lazy.scrolls == 5


def test_a_changing_row_set_with_the_same_count_is_not_mistaken_for_progress():
    """Only growth counts. The same number of rows is 'nothing added', however long the page keeps rendering."""
    lazy = LazyList(stuck_at=12)
    assert not run(lazy).complete


def test_the_scroll_script_only_scrolls_the_services_list():
    assert "services-option-list" in SCROLL_SERVICES_JS and "scrollTop" in SCROLL_SERVICES_JS
    for banned in (".click(", "dispatchEvent", ".value =", "setAttribute", "fetch(", "location", "innerHTML ="):
        assert banned not in SCROLL_SERVICES_JS, banned


@pytest.mark.parametrize("filtered_count", [0, 3, 12])
def test_a_filtered_search_result_is_never_a_complete_list(filtered_count):
    assert not parse_services(page(ROWS[:filtered_count], 63)).complete
