import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import ApiError, GridService, Store

W1 = {"window_start": "2026-09-26T10:00:00Z", "window_end": "2026-09-26T12:00:00Z"}


class ReservationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.s = GridService(Store(Path(self.tmp.name) / "g.db"))
        self.sub = self.s.register_asset("d", "dispatcher", "SUB", "中心站", "substation", 200, "A")
        self.line = self.s.register_asset("d", "dispatcher", "LINE", "线路", "line", 100, "A", self.sub["id"])
        self.back = self.s.register_asset("d", "dispatcher", "BACK", "备用电源", "backup", 60, "A", self.sub["id"])

    def tearDown(self): self.s.store.close(); self.tmp.cleanup()

    def submitted(self, code, steps):
        outage = self.s.create_outage("d", "dispatcher", code, f"事故{code}", ["A"])
        plan = self.s.create_plan("d", "dispatcher", outage["id"], steps)
        return outage, self.s.submit_plan("d", "dispatcher", plan["id"], plan["revision"])

    def activate_and_confirm(self, plan, step_no, tag):
        plan = self.s.activate_plan("d", "dispatcher", plan["id"], plan["revision"])
        self.s.field_report("f", "field", plan["id"], step_no, tag, plan["version"], "completed")
        self.s.confirm_step("d", "dispatcher", plan["id"], step_no, "confirmed")
        return plan

    def test_approve_holds_and_conflict_stays_pending(self):
        _, p1 = self.submitted("OUT-1", [{"seq": 1, "asset": "LINE", "required_mw": 70, "critical": True, **W1}])
        approved = self.s.approve_plan("d", "dispatcher", p1["id"], p1["revision"])
        self.assertEqual("approved", approved["state"])
        self.assertEqual("held", approved["reservations"][0]["state"])
        self.assertEqual(70.0, approved["reservations"][0]["required_mw"])

        _, p2 = self.submitted("OUT-2", [{"seq": 1, "asset": "LINE", "required_mw": 50,
                                          "window_start": "2026-09-26T11:00:00Z", "window_end": "2026-09-26T13:00:00Z"}])
        blocked = self.s.approve_plan("d", "dispatcher", p2["id"], p2["revision"])
        self.assertEqual("submitted", blocked["state"])  # 资源不够留在待批准区
        conflicts = blocked["capacity_check"]["conflicts"]
        self.assertEqual(1, len(conflicts))
        competitor = conflicts[0]["competitors"][0]
        self.assertEqual("OUT-1", competitor["incident_code"])  # 冲突事故
        self.assertEqual({"start": W1["window_start"], "end": W1["window_end"]}, competitor["window"])  # 冲突时间段
        self.assertEqual(20.0, conflicts[0]["shortage_mw"])

        # 窗口不重叠的计划可以批准
        _, p3 = self.submitted("OUT-3", [{"seq": 1, "asset": "LINE", "required_mw": 50,
                                          "window_start": "2026-09-26T13:00:00Z", "window_end": "2026-09-26T14:00:00Z"}])
        self.assertEqual("approved", self.s.approve_plan("d", "dispatcher", p3["id"], p3["revision"])["state"])

    def test_plan_change_releases_old_holds(self):
        _, p1 = self.submitted("OUT-1", [{"seq": 1, "asset": "LINE", "required_mw": 70, "critical": True, **W1}])
        p1 = self.s.approve_plan("d", "dispatcher", p1["id"], p1["revision"])
        _, p2 = self.submitted("OUT-2", [{"seq": 1, "asset": "LINE", "required_mw": 50, **W1}])
        self.assertEqual("submitted", self.s.approve_plan("d", "dispatcher", p2["id"], p2["revision"])["state"])

        # 计划变更：改用备用电源，旧占位（LINE 70MW）应释放
        changed = self.s.make_plan_change("d", "dispatcher", p1["id"], [
            {"seq": 1, "asset": "BACK", "required_mw": 50, "critical": True, **W1}], p1["revision"])
        self.assertEqual([], [r for r in self.s.state()["reservations"] if r["plan_id"] == p1["id"]])
        changed = self.s.submit_plan("d", "dispatcher", changed["id"], changed["revision"])
        self.assertEqual("approved", self.s.approve_plan("d", "dispatcher", changed["id"], changed["revision"])["state"])

        # LINE 已腾空，之前被挡的计划现在能批准
        retry = self.s.approve_plan("d", "dispatcher", p2["id"], p2["revision"])
        self.assertEqual("approved", retry["state"])

    def test_critical_confirm_consumes_capacity(self):
        _, p1 = self.submitted("OUT-1", [{"seq": 1, "asset": "LINE", "required_mw": 70, "critical": True, **W1}])
        p1 = self.s.approve_plan("d", "dispatcher", p1["id"], p1["revision"])
        p1 = self.activate_and_confirm(p1, 1, "r-1")
        self.assertEqual("consumed", self.s.state()["reservations"][0]["state"])

        # 计划变更后已消耗容量仍然锁定，后续计划抢不到
        changed = self.s.make_plan_change("d", "dispatcher", p1["id"], [
            {"seq": 1, "asset": "LINE", "required_mw": 70, "critical": True, **W1},
            {"seq": 2, "asset": "BACK", "required_mw": 40, "depends_on": [1],
             "window_start": "2026-09-26T12:00:00Z", "window_end": "2026-09-26T13:00:00Z"}], p1["revision"])
        self.assertEqual("consumed", self.s.state()["reservations"][0]["state"])
        changed = self.s.submit_plan("d", "dispatcher", changed["id"], changed["revision"])
        self.assertEqual("approved", self.s.approve_plan("d", "dispatcher", changed["id"], changed["revision"])["state"])

        _, p2 = self.submitted("OUT-2", [{"seq": 1, "asset": "LINE", "required_mw": 50,
                                          "window_start": "2026-09-26T10:30:00Z", "window_end": "2026-09-26T11:30:00Z"}])
        blocked = self.s.approve_plan("d", "dispatcher", p2["id"], p2["revision"])
        self.assertEqual("submitted", blocked["state"])
        self.assertEqual("consumed", blocked["capacity_check"]["conflicts"][0]["competitors"][0]["state"])

    def test_noncritical_confirm_releases_hold(self):
        _, p1 = self.submitted("OUT-1", [{"seq": 1, "asset": "LINE", "required_mw": 70, **W1}])
        p1 = self.s.approve_plan("d", "dispatcher", p1["id"], p1["revision"])
        self.activate_and_confirm(p1, 1, "r-1")
        self.assertEqual([], self.s.state()["reservations"])  # 非关键步骤完成后释放
        _, p2 = self.submitted("OUT-2", [{"seq": 1, "asset": "LINE", "required_mw": 50, **W1}])
        self.assertEqual("approved", self.s.approve_plan("d", "dispatcher", p2["id"], p2["revision"])["state"])

    def test_same_plan_steps_stack_in_window(self):
        _, p1 = self.submitted("OUT-1", [
            {"seq": 1, "asset": "LINE", "required_mw": 60, **W1},
            {"seq": 2, "asset": "LINE", "required_mw": 60, "depends_on": [1], **W1}])
        blocked = self.s.approve_plan("d", "dispatcher", p1["id"], p1["revision"])
        self.assertEqual("submitted", blocked["state"])
        self.assertEqual(2, len(blocked["capacity_check"]["conflicts"]))
        self.assertEqual([2], blocked["capacity_check"]["conflicts"][0]["same_plan_steps"])

    def test_capacity_board_hints(self):
        _, p1 = self.submitted("OUT-1", [{"seq": 1, "asset": "LINE", "required_mw": 70, "critical": True, **W1}])
        self.s.approve_plan("d", "dispatcher", p1["id"], p1["revision"])
        _, p2 = self.submitted("OUT-2", [{"seq": 1, "asset": "LINE", "required_mw": 50,
                                          "window_start": "2026-09-26T11:00:00Z", "window_end": "2026-09-26T13:00:00Z"}])
        self.s.approve_plan("d", "dispatcher", p2["id"], p2["revision"])  # 容量不足，留在待批准区
        board = self.s.capacity_board()
        line = next(a for a in board["assets"] if a["code"] == "LINE")
        self.assertEqual(70.0, line["held_mw"])
        self.assertEqual(30.0, line["available_mw"])
        self.assertEqual("OUT-1", line["reservations"][0]["incident_code"])
        pending = {p["plan_id"]: p for p in board["pending_plans"]}
        self.assertFalse(pending[p2["id"]]["fits"])
        self.assertEqual("容量不足，留在待批准区", pending[p2["id"]]["hint"])
        self.assertIn("OUT-1", str(pending[p2["id"]]["conflicts"]))

    def test_invalid_window_rejected(self):
        with self.assertRaises(ApiError):
            self.s.create_plan("d", "dispatcher",
                               self.s.create_outage("d", "dispatcher", "OUT-9", "事故", ["A"])["id"],
                               [{"seq": 1, "asset": "LINE", "required_mw": 10,
                                 "window_start": "2026-09-26T12:00:00Z", "window_end": "2026-09-26T10:00:00Z"}])


if __name__ == "__main__": unittest.main()
