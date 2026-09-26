# 电网事故应急与恢复调度系统

标准库 Python 3.11+ + SQLite。系统管理停运事故、重要用户、备用容量、恢复步骤及安全依赖；接受现场离线报告并区分已合并、版本冲突和受保护记录，异常遥测单独隔离。多起停电并发时，恢复容量通过预占台账协调，避免计划抢同一线路和备用电源。

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
- `POST /api/plans/{id}/change`：在不修改已确认步骤的前提下创建新计划版本。
- `POST /api/field-reports`：合并现场离线报告，重复客户端编号不会重复写入。
- `POST /api/plans/{id}/confirm`：调度员确认步骤，依赖未满足时拒绝。
- `POST /api/status`：发布当前恢复状态。
- `GET /api/plans/{id}`、`GET /api/state`、`GET /api/capacity`、`GET /api/health`：详情、状态、容量台账和健康检查。

## 恢复容量预占

容量判定、预占台账、页面提示分在三块业务代码（`CapacityAdvisor`、`ReservationLedger`、`static/index.html`）：

- 计划步骤可带恢复窗口 `window_start`/`window_end`（成对出现，缺省视为全程占用）。
- 批准时按步骤资产、容量和恢复窗口占位；窗口内可用容量不足则返回 409 及冲突事故与时间段，计划留在待批准区（`submitted`），容量释放后可重试批准。
- 计划变更或被新版本替换时释放旧占位；已确认步骤的容量消耗结转到新版本，不重复占位。
- 关键步骤现场确认后，该步骤容量转为已消耗，后续事故的计划抢不到。
- 页面与 `GET /api/capacity` 展示各资产当前占用（预占/已消耗/可用）、台账明细和待批准计划的冲突原因。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

当前为原型：容量和依赖是静态安全模型，不包含潮流计算、SCADA/EMS 协议、实时遥测质量码或生产级多实例锁；离线合并通过客户端编号和计划版本完成。
