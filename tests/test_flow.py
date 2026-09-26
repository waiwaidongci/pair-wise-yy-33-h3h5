import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import ApiError, GridService, Store


class GridFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.s = GridService(Store(Path(self.tmp.name) / "g.db"))
        self.sub = self.s.register_asset("dispatcher", "dispatcher", "SUB", "中心站", "substation", 200, "A")
        self.line = self.s.register_asset("dispatcher", "dispatcher", "LINE", "线路", "line", 100, "A", self.sub["id"])
        self.s.register_facility("dispatcher", "dispatcher", "医院", "hospital", self.sub["id"], 1, 50)

    def tearDown(self): self.s.store.close(); self.tmp.cleanup()

    def plan(self, code="OUT-1"):
        outage = self.s.create_outage("dispatcher", "dispatcher", code, "线路跳闸", ["A"])
        plan = self.s.create_plan("dispatcher", "dispatcher", outage["id"], [
            {"seq": 1, "action": "检查", "asset": "SUB", "required_mw": 80, "critical": True},
            {"seq": 2, "action": "送电", "asset": "LINE", "required_mw": 70, "depends_on": [1], "critical": True}])
        plan = self.s.submit_plan("dispatcher", "dispatcher", plan["id"], plan["revision"])
        plan = self.s.approve_plan("dispatcher", "dispatcher", plan["id"], plan["revision"], "安全校核通过")
        return outage, self.s.activate_plan("dispatcher", "dispatcher", plan["id"], plan["revision"])

    def test_full_restore_offline_merge_duplicate_and_plan_change(self):
        outage, plan = self.plan()
        report = self.s.field_report("field", "field", plan["id"], 1, "client-1", plan["version"], "completed", "设备已检查")
        self.assertEqual("merged", report["merge_status"])
        confirmed = self.s.confirm_step("dispatcher", "dispatcher", plan["id"], 1, "confirmed", "现场照片核验")
        self.assertEqual("confirmed", confirmed["status"])
        protected = self.s.field_report("field", "field", plan["id"], 1, "client-1-protected", plan["version"], "blocked", "补充遥测")
        self.assertEqual("protected", protected["merge_status"])
        plan2 = self.s.make_plan_change("dispatcher", "dispatcher", plan["id"], [
            {"seq": 1, "action": "检查", "asset": "SUB", "required_mw": 80, "critical": True},
            {"seq": 2, "action": "送电", "asset": "LINE", "required_mw": 70, "depends_on": [1], "critical": True}], plan["revision"])
        detail = self.s.plan_detail(plan2["id"])
        self.assertEqual(1, len(detail["confirmations"]))
        plan2 = self.s.submit_plan("dispatcher", "dispatcher", plan2["id"], plan2["revision"])
        plan2 = self.s.approve_plan("dispatcher", "dispatcher", plan2["id"], plan2["revision"])
        plan2 = self.s.activate_plan("dispatcher", "dispatcher", plan2["id"], plan2["revision"])
        self.s.field_report("field", "field", plan2["id"], 2, "client-2", plan2["version"], "completed", "已送电")
        self.s.confirm_step("dispatcher", "dispatcher", plan2["id"], 2, "confirmed")
        status = self.s.publish_status("dispatcher", "dispatcher", outage["id"], plan2["id"])
        self.assertEqual("restored", status["status"]["state"])

    def test_anomaly_stale_report_dependency_and_permissions(self):
        outage, plan = self.plan("OUT-2")
        anomaly = self.s.record_telemetry("operator", "operator", self.line["id"], 500, 220, "2026-09-24T00:00:00Z")
        self.assertFalse(anomaly["valid"])
        with self.assertRaises(ApiError):
            self.s.field_report("operator", "operator", plan["id"], 1, "bad-role", plan["version"], "completed")
        stale = self.s.field_report("field", "field", plan["id"], 2, "stale", plan["version"] - 1, "completed")
        self.assertEqual("conflict", stale["merge_status"])
        with self.assertRaises(ApiError):
            self.s.confirm_step("dispatcher", "dispatcher", plan["id"], 2, "confirmed")
        with self.assertRaises(ApiError):
            self.s.create_plan("dispatcher", "dispatcher", outage["id"], [{"seq": 1, "action": "送电", "asset": "LINE", "required_mw": 101}])


class CapacityLedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.s = GridService(Store(Path(self.tmp.name) / "g.db"))
        self.sub = self.s.register_asset("dispatcher", "dispatcher", "SUB", "中心站", "substation", 200, "A")
        self.line = self.s.register_asset("dispatcher", "dispatcher", "LINE", "线路", "line", 100, "A", self.sub["id"])

    def tearDown(self): self.s.store.close(); self.tmp.cleanup()

    def submitted_plan(self, code, steps):
        outage = self.s.create_outage("dispatcher", "dispatcher", code, "故障", ["A"])
        plan = self.s.create_plan("dispatcher", "dispatcher", outage["id"], steps)
        return self.s.submit_plan("dispatcher", "dispatcher", plan["id"], plan["revision"])

    def test_approval_reserves_and_conflict_stays_pending(self):
        plan_a = self.submitted_plan("OUT-A", [
            {"seq": 1, "asset": "LINE", "required_mw": 70, "critical": True,
             "window_start": "2026-09-26T00:00:00Z", "window_end": "2026-09-26T04:00:00Z"}])
        plan_a = self.s.approve_plan("dispatcher", "dispatcher", plan_a["id"], plan_a["revision"])
        reservation = self.s.plan_detail(plan_a["id"])["reservations"][0]
        self.assertEqual("reserved", reservation["state"])
        self.assertEqual("2026-09-26T00:00:00Z", reservation["window_start"])

        plan_b = self.submitted_plan("OUT-B", [
            {"seq": 1, "asset": "LINE", "required_mw": 50,
             "window_start": "2026-09-26T02:00:00Z", "window_end": "2026-09-26T06:00:00Z"}])
        with self.assertRaises(ApiError) as ctx:
            self.s.approve_plan("dispatcher", "dispatcher", plan_b["id"], plan_b["revision"])
        self.assertEqual(409, ctx.exception.status)
        conflict = ctx.exception.details["conflicts"][0]
        self.assertEqual("OUT-A", conflict["conflicts_with"][0]["incident_code"])
        self.assertEqual(["2026-09-26T00:00:00Z", "2026-09-26T04:00:00Z"], conflict["conflicts_with"][0]["window"])
        self.assertEqual("submitted", self.s.plan_detail(plan_b["id"])["plan"]["state"])  # 留在待批准区

        # 恢复窗口不重叠的计划可以批准
        plan_c = self.submitted_plan("OUT-C", [
            {"seq": 1, "asset": "LINE", "required_mw": 50,
             "window_start": "2026-09-26T05:00:00Z", "window_end": "2026-09-26T06:00:00Z"}])
        self.s.approve_plan("dispatcher", "dispatcher", plan_c["id"], plan_c["revision"])

        # 计划变更释放旧占位后，待批准计划可以抢到容量（C 占 50，B 需 50，刚好够）
        changed = self.s.make_plan_change("dispatcher", "dispatcher", plan_a["id"], [
            {"seq": 1, "asset": "LINE", "required_mw": 70, "critical": True,
             "window_start": "2026-09-26T00:00:00Z", "window_end": "2026-09-26T04:00:00Z"}], plan_a["revision"])
        self.assertEqual("draft", changed["state"])
        self.assertEqual(["released"], [r["state"] for r in self.s.plan_detail(plan_a["id"])["reservations"]])
        plan_b = self.s.approve_plan("dispatcher", "dispatcher", plan_b["id"], plan_b["revision"])
        self.assertEqual("approved", plan_b["state"])

    def test_critical_confirmation_consumes_capacity(self):
        plan = self.submitted_plan("OUT-D", [
            {"seq": 1, "asset": "SUB", "required_mw": 80, "critical": True},
            {"seq": 2, "asset": "SUB", "required_mw": 20},
            {"seq": 3, "asset": "LINE", "required_mw": 70, "depends_on": [1]}])
        plan = self.s.approve_plan("dispatcher", "dispatcher", plan["id"], plan["revision"])
        plan = self.s.activate_plan("dispatcher", "dispatcher", plan["id"], plan["revision"])
        self.s.field_report("field", "field", plan["id"], 1, "r1", plan["version"], "completed")
        self.s.confirm_step("dispatcher", "dispatcher", plan["id"], 1, "confirmed")
        self.s.field_report("field", "field", plan["id"], 2, "r2", plan["version"], "completed")
        self.s.confirm_step("dispatcher", "dispatcher", plan["id"], 2, "confirmed")
        states = {r["step_no"]: r["state"] for r in self.s.plan_detail(plan["id"])["reservations"]}
        self.assertEqual("consumed", states[1])  # 关键步骤现场确认 → 已消耗
        self.assertEqual("reserved", states[2])  # 非关键步骤不转消耗
        self.assertEqual("reserved", states[3])

        # 已消耗容量后续计划抢不到：SUB 200 - 80(消耗) - 20(预占) = 100 < 150
        plan_e = self.submitted_plan("OUT-E", [{"seq": 1, "asset": "SUB", "required_mw": 150}])
        with self.assertRaises(ApiError) as ctx:
            self.s.approve_plan("dispatcher", "dispatcher", plan_e["id"], plan_e["revision"])
        self.assertEqual("consumed", ctx.exception.details["conflicts"][0]["conflicts_with"][0]["state"])

        overview = self.s.capacity_overview()
        sub = next(a for a in overview["assets"] if a["code"] == "SUB")
        self.assertEqual((80.0, 20.0, 100.0), (sub["consumed_mw"], sub["reserved_mw"], sub["available_mw"]))
        self.assertEqual("OUT-E", overview["pending_conflicts"][0]["incident_code"])

        # 计划变更：已消耗保留，未确认步骤的预占释放；新版本只重新占位未确认步骤
        changed = self.s.make_plan_change("dispatcher", "dispatcher", plan["id"], [
            {"seq": 1, "asset": "SUB", "required_mw": 80, "critical": True},
            {"seq": 2, "asset": "SUB", "required_mw": 20},
            {"seq": 3, "asset": "LINE", "required_mw": 70, "depends_on": [1]}], plan["revision"])
        old_states = {r["step_no"]: r["state"] for r in self.s.plan_detail(plan["id"])["reservations"]}
        self.assertEqual("consumed", old_states[1])
        self.assertEqual("released", old_states[2])
        self.assertEqual("released", old_states[3])
        changed = self.s.submit_plan("dispatcher", "dispatcher", changed["id"], changed["revision"])
        changed = self.s.approve_plan("dispatcher", "dispatcher", changed["id"], changed["revision"])
        new_states = {r["step_no"]: r["state"] for r in self.s.plan_detail(changed["id"])["reservations"]}
        self.assertEqual({3: "reserved"}, new_states)

    def test_restore_window_validation(self):
        outage = self.s.create_outage("dispatcher", "dispatcher", "OUT-W", "故障", ["A"])
        with self.assertRaises(ApiError):
            self.s.create_plan("dispatcher", "dispatcher", outage["id"], [
                {"seq": 1, "asset": "LINE", "required_mw": 10, "window_start": "2026-09-26T00:00:00Z"}])
        with self.assertRaises(ApiError):
            self.s.create_plan("dispatcher", "dispatcher", outage["id"], [
                {"seq": 1, "asset": "LINE", "required_mw": 10,
                 "window_start": "2026-09-26T06:00:00Z", "window_end": "2026-09-26T05:00:00Z"}])


if __name__ == "__main__": unittest.main()
