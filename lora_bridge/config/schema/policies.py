from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class EgressRate(BaseModel):
    msgs_per_window: int
    window_seconds: float


class ReconnectBackoff(BaseModel):
    base: float = 2
    max: float = 60
    jitter: bool = True


class LabelPolicy(BaseModel):
    include_type: Literal["auto", "always", "never"] = "auto"
    max_nick_bytes: int = 24
    on_oversize: Literal["reject"] = "reject"   # НЕ truncate (AD-11)


class NodePolicies(BaseModel):
    egress_rate: EgressRate
    queue_ttl_seconds: float = 45
    commit_timeout_seconds: float = 30
    reconnect_backoff: ReconnectBackoff = ReconnectBackoff()
    dedup_ttl_seconds: float = 300
    drop_notice_window_seconds: float = 60
    label: LabelPolicy = LabelPolicy()
