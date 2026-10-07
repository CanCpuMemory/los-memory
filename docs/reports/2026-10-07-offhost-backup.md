# W-05 异机加密备份与恢复演练

日期：2026-10-07。范围：M3 影子库 → 同城异地群晖（ssh alias `syno`，DS716+II，DSM 7.2.1）。全程未改 M3 现网库、未改 NAS 服务配置。

实现：`scripts/shadow_backup.py`（`backup` / `restore-drill` / `status`）。自动化：M1 launchd `com.echerlos.los-memory-shadow-backup`，每日 04:30。

## 结论

| 门槛 | 目标 | 实测 | 判定 |
| --- | --- | --- | --- |
| 异机 | 独立于 M3 的主机 | 群晖 DS716+II（同城异地），`/volume1` 余量 2.6 TiB | ✅ |
| 加密 | 落盘加密 | AES-256-CBC + PBKDF2（iter 200000，随机 salt），密钥 `~/.local/share/los-memory-shadow/backup.key`（0600，32 字节随机） | ✅ |
| 字节一致 | 异地副本与本地加密件同哈希 | `readback_ok: true`，`59981856` 字节逐字节一致（NAS 侧 `sha256sum` 比对） | ✅ |
| RPO ≤ 24h | 每日备份 | launchd 每日 04:30；`status` 报 `newest_age_hours` 与 `rpo_within_gate` | ✅（机制就绪，需连续观察） |
| RTO ≤ 1h | 冷恢复计时 | **12.92 s**（下载 11.09 s + 解密 12.59 s），`rto_within_gate: true` | ✅ |
| 摘要一致 | `(space,source_id,digest,active)` 一致 | 恢复件身份摘要 `90e5316b…` 与 M3 在线摘要**完全相同**，`integrity_check = ok`，2048 条 | ✅ |

## 为什么不是 restic（实测，不是假设）

原计划是 restic。落地时实测发现目标端**不具备可用后端**：

| 探测 | 结果 |
| --- | --- |
| `sftp syno` | `Connection closed` —— SFTP 子系统未启用 |
| 端口 | 22（纯 SSH）可达；FTP 21、SMB 445 开放；WebDAV 5005/5006 **关闭** |
| `sudo -n true`（syno 登录用户） | `sudo: a password is required` —— 无法自行启用 SFTP 服务 |
| restic 0.19.1 可用后端 | `sftp:` 不可用；`ftp:`/`smb:` 缺凭据；`rest:` 需在 NAS 跑容器 |

因此改用**今天就能证实的传输**：一致性快照 → SSH 流 → 落盘加密。若日后在 DSM 里启用 SFTP（控制面板 → 文件服务 → FTP → SFTP），把 `upload`/`fetch` 换成 restic 是局部改动，快照、校验与演练逻辑不变。

## 实现要点

1. **一致性**：不是复制文件（现网库在 WAL 下，直接拷会得到撕裂副本），而是 M3 上用 SQLite backup API 生成一致快照（实测 0.065–0.072 s）。快照走 `/tmp`，用完删除，不在 M3 留副本。
2. **先验后传**：本地比对快照与 M3 在线身份摘要，再做 `PRAGMA integrity_check`；不一致直接中止，不上传坏数据。
3. **端到端回读**：上传后从 NAS 取回 `sha256sum` 与本地加密件比对，字节数也比对 —— 证明"异地那份就是刚加密的那份"，而不是只信 `cat` 的退出码。
4. **保留策略**：近 7 天每日 + 4 周每周 + 6 月每月（`retention_keep`）。**策略本身有测试钉住**（`tests/unit/test_shadow_backup_retention.py`）：用 400 个每日文件实测保留 **18 个**（年龄 0–10 天每份、之后 17/24/37/68/99/129/160 天各一份，覆盖 7 个自然月），最新一份永不删除，200 天以上的不保留。按单份 57.2 MiB 计，稳态约 **1.0 GiB**。这里写实测而不是估算：策略出错等于删备份，所以不能只靠读代码。
5. **台账**：`~/.local/share/los-memory-shadow/backup-ledger.jsonl` 记 `bytes` / 双哈希 / `identity_digest` / `records` / `rpo_seconds` / `total_seconds`，供 W-07 报告引用。

## 证据

```sh
# 首次备份（16.25 s 往返）
python3 scripts/shadow_backup.py backup
{"name": "shadow-20261007T042407Z.sqlite3.enc", "bytes": 59981856,
 "encrypted_sha256": "2029802732f0…", "plaintext_sha256": "03b92609db65…",
 "identity_digest": "90e5316bd095…", "records": 2048, "integrity": "ok",
 "readback_ok": true, "snapshot_seconds": 0.065, "total_seconds": 16.25}

# 冷恢复演练（只从 NAS 取）
python3 scripts/shadow_backup.py restore-drill
{"integrity": "ok", "records_restored": 2048, "records_expected": 2048,
 "identity_digest": "90e5316bd095…", "reference_digest": "90e5316bd095…",
 "digest_match": true, "download_seconds": 11.09, "decrypt_seconds": 12.59,
 "rto_seconds": 12.92, "rto_within_gate": true}

# launchd 实跑（不是只写 plist）
launchctl print gui/$(id -u)/com.echerlos.los-memory-shadow-backup | grep -E "runs|last exit"
  runs = 1 ; last exit code = 0
ssh syno 'ls -la $HOME/los-memory-shadow-backup/'
  shadow-20261007T042407Z.sqlite3.enc   59981856
  shadow-20261007T042448Z.sqlite3.enc   59981856
```

## 限制与如实记录

- **AES-CBC 不提供认证**（`openssl enc` 不支持 AEAD）。落盘篡改的检出靠两条：加密件 sha256 回读比对，以及恢复后身份摘要与 `integrity_check`。攻击者改写 NAS 上的密文会导致解密后摘要不匹配而被判失败，但**不会**在解密瞬间报"被篡改"——这是 CBC 的固有限制，必须在报告里说清楚，不能宣称"带认证加密"。
- **无去重**：每日全量 57.2 MiB。按实测保留策略（400 天输入只留 18 份）稳态约 1.0 GiB，可接受；若日后影子库增长到 GB 级，应换成 restic/kopia 的增量去重。
- **RPO 的"机制就绪"不等于"已连续满足"**：launchd 已实跑一次且退出码 0，但 24 小时 RPO 需要连续多日观察（W-07 报告会纳入 `newest_age_hours`）。
- 密钥**只在本机**，未在任何备份里；丢失密钥等于备份不可恢复。这符合"密钥与服务分离"，但需要用户知晓：异地备份不含密钥托管。
- 未做**整机恢复**演练（M3 重装后重建影子服务）；本次证明的是"数据可恢复"，不是"服务可重建"。
