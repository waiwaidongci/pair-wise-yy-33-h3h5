# 电网事故应急与恢复调度系统

标准库 Python 3.11+ + SQLite。系统管理停运事故、重要用户、备用容量、恢复步骤及安全依赖；接受现场离线报告并区分已合并、版本冲突和受保护记录，异常遥测单独隔离。内置恢复容量预占台：多起停电的恢复计划按步骤资产、容量和恢复窗口抢占线路与备用电源，不再需要口头协调。

## 运行

```bash
python3 app.py --init --seed
python3 app.py
```

默认端口 `8215`。身份使用 `X-Actor` 和 `X-Role`，角色为 `dispatcher`、`operator`、`field`。可用 `--port`、`--db` 覆盖。

## 主要接口

- `POST /api/assets`、`POST /api/facilities`：登记线路资产和医院等重要用户。
- `POST /api/outages`：创建或幂等接收同一事故。
- `POST /api/telemetry`：记录并隔离错误遥测。
- `POST /api/plans`、`/submit`、`/approve`、`/activate`：创建、提交、审批并启用安全恢复计划。
- `POST /api/plans/{id}/change`：在不修改已确认步骤的前提下创建新计划版本，同时释放旧版本预占。
- `POST /api/field-reports`：合并现场离线报告，重复客户端编号不会重复写入。
- `POST /api/plans/{id}/confirm`：调度员确认步骤，依赖未满足时拒绝；关键步骤确认后容量转为已消耗。
- `POST /api/status`：发布当前恢复状态。
- `GET /api/plans/{id}`、`GET /api/state`、`GET /api/capacity`、`GET /api/health`：详情、状态、容量看板和健康检查。

## 恢复容量预占台

计划步骤可带 `window_start` / `window_end`（ISO 时间，可空表示不限窗口）。容量判定、预占台账和页面提示分为三块业务代码：`CapacityAdvisor`、`ReservationLedger`、`ReservationHints`。

- **批准占位**：`approve` 时按步骤资产、容量和恢复窗口做容量判定，通过则写入预占（`held`）。容量不足时计划保持 `submitted` 留在待批准区，响应的 `capacity_check.conflicts` 列出冲突事故编号、时间段、缺口容量和竞争计划。
- **变更释放**：计划变更（`change`）或被同事故新计划取代时，旧版本的预占转为 `released`；已消耗记录保留。
- **确认消耗**：关键步骤（`critical`）现场确认后，对应预占转为 `consumed`，后续计划在重叠窗口内抢不到这部分容量；非关键步骤确认后释放其预占。
- **页面看板**：首页和 `GET /api/capacity` 展示各资产当前预占/已消耗/可用容量、占用明细（事故、计划、步骤、窗口），以及待批准计划的实时冲突原因。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

当前为原型：容量和依赖是静态安全模型，不包含潮流计算、SCADA/EMS 协议、实时遥测质量码或生产级多实例锁；离线合并通过客户端编号和计划版本完成。
