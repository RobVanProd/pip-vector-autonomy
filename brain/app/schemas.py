from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter


class SayAction(BaseModel):
    type: Literal["say"]
    text: str = Field(min_length=1, max_length=180)


class DriveAction(BaseModel):
    type: Literal["drive"]
    speed_mmps: int = Field(ge=-80, le=80)
    duration_ms: int = Field(ge=100, le=2000)


class TurnAction(BaseModel):
    type: Literal["turn"]
    degrees: int = Field(ge=-90, le=90)


class HeadAction(BaseModel):
    type: Literal["head"]
    angle_deg: int = Field(ge=-20, le=40)


class LiftAction(BaseModel):
    type: Literal["lift"]
    height: Literal["low", "medium", "high"]


class AnimationAction(BaseModel):
    type: Literal["animation"]
    name: str = Field(pattern=r"^[a-zA-Z0-9_\-:.]{1,80}$")


class BehaviorAction(BaseModel):
    type: Literal["behavior"]
    name: Literal[
        "look_around",
        "find_faces",
        "connect_cube",
        "roll_visible_cube",
        "go_home",
        "drive_off_charger",
    ]


class StopAction(BaseModel):
    type: Literal["stop"]


class ListenAction(BaseModel):
    type: Literal["listen"]
    reason: str = Field(default="autonomous speech follow-up", max_length=120)


Action = Annotated[
    Union[
        SayAction,
        DriveAction,
        TurnAction,
        HeadAction,
        LiftAction,
        AnimationAction,
        BehaviorAction,
        StopAction,
        ListenAction,
    ],
    Field(discriminator="type"),
]
ActionList = TypeAdapter(list[Action])


class RobotState(BaseModel):
    connected: bool | None = None
    on_charger: bool | None = None
    charging: bool | None = None
    calm_power_mode: bool | None = None
    sleeping: bool | None = None
    picked_up: bool | None = None
    being_held: bool | None = None
    cliff_detected: bool | None = None
    obstacle_close: bool | None = None
    face_detected: bool | None = None      # populated by SDK if available
    cube_detected: bool | None = None      # populated by SDK if available
    low_battery: bool | None = None
    behavior_control_granted: bool | None = None
    battery_volts: float | None = None
    battery_level: int | str | None = None
    battery_percent: float | None = None
    battery_percent_source: str | None = None
    pose: dict | None = None
    head_angle_deg: float | None = None
    lift_height_mm: float | None = None
    vision_description: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=1000)


class PlanRequest(BaseModel):
    user_text: str = Field(min_length=1, max_length=5000)
    robot_state: RobotState = Field(default_factory=RobotState)


class PlanResponse(BaseModel):
    model: str
    actions: list[Action]
    denied_actions: list[dict] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    raw: str
    execution_mode: str


class ExecuteRequest(BaseModel):
    actions: list[Action]
    robot_state: RobotState = Field(default_factory=RobotState)
    dry_run: bool = True


class ExecuteResponse(BaseModel):
    ok: bool
    mode: str
    executed: list[dict] = Field(default_factory=list)
    denied_actions: list[dict] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)


class PlanExecuteRequest(PlanRequest):
    dry_run: bool = True


class ChatRequest(BaseModel):
    user_text: str = Field(min_length=1, max_length=2500)
    execute: bool = False
    dry_run: bool = True
    robot_state: RobotState = Field(default_factory=RobotState)


class MemoryFactRequest(BaseModel):
    fact: str = Field(min_length=1, max_length=300)


class AutonomyConfig(BaseModel):
    enabled: bool = False
    dry_run: bool = True
    interval_seconds: int = Field(default=45, ge=15, le=300)
    allow_motion: bool = False
    listen_after_speech: bool = False
    respect_sleep: bool = True
    include_vision: bool = True
    vision_interval_ticks: int = Field(default=2, ge=1, le=20)
    speak_probability: float = Field(default=0.25, ge=0.0, le=1.0)
    vibe: str = Field(default="curious desk companion", max_length=120)
    robot_state: RobotState = Field(default_factory=RobotState)


class AutonomyStatus(BaseModel):
    enabled: bool
    dry_run: bool
    interval_seconds: int
    allow_motion: bool
    listen_after_speech: bool
    respect_sleep: bool
    include_vision: bool
    vision_interval_ticks: int
    speak_probability: float
    vibe: str
    ticks: int
    last_plan: PlanResponse | None = None
    last_execute: ExecuteResponse | None = None
    last_error: str | None = None


class VisionConfig(BaseModel):
    enabled: bool = False
    interval_seconds: int = Field(default=20, ge=10, le=300)
    respect_sleep: bool = True


class VisionStatus(BaseModel):
    enabled: bool
    interval_seconds: int
    respect_sleep: bool
    ticks: int
    last_capture_ts: float | None = None
    latest_age_seconds: float | None = None
    latest_vision: dict | None = None
    last_result: dict | None = None
    last_error: str | None = None


class VoiceBridgeConfig(BaseModel):
    enabled: bool = False
    dry_run: bool = False
    allow_motion: bool = False
    use_behavior_control: bool = False
    route_intents_to_gemma: bool = True
    listen_after_speech: bool = False   # open Whisper reply window after Pip speaks via voice bridge


class VoiceBridgeStatus(BaseModel):
    enabled: bool
    connected: bool = False
    dry_run: bool
    allow_motion: bool
    use_behavior_control: bool
    route_intents_to_gemma: bool
    listen_after_speech: bool = False
    last_wake: dict | None = None
    last_intent: dict | None = None
    last_error: str | None = None


class WirePodTranscriptRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2500)
    serial: str | None = Field(default=None, max_length=32)
    locale: str | None = Field(default=None, max_length=24)
    source: str = Field(default="wirepod-custom-intent", max_length=80)
    execute: bool = True
    dry_run: bool = False


class ListenerConfig(BaseModel):
    enabled: bool = False
    auto_route: bool = False
    execute: bool = False
    dry_run: bool = True
    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    vad_mode: int = Field(default=2, ge=0, le=3)
    frame_ms: int = Field(default=20, ge=10, le=30)
    min_speech_ms: int = Field(default=300, ge=100, le=3000)
    silence_ms: int = Field(default=700, ge=200, le=3000)
    max_utterance_ms: int = Field(default=8000, ge=1000, le=30000)
    pre_roll_ms: int = Field(default=300, ge=0, le=1000)
    min_rms: int = Field(default=120, ge=0, le=10000)
    stt_model: str = Field(default="tiny.en", max_length=120)
    language: str = Field(default="en", max_length=12)
    compute_type: str = Field(default="int8", max_length=40)
    mute_after_route_seconds: float = Field(default=5.0, ge=0.0, le=30.0)


class SentinelConfig(BaseModel):
    enabled: bool = False
    dry_run: bool = True
    allow_motion: bool = False
    poll_interval_seconds: float = Field(default=2.0, ge=1.0, le=30.0)
    listen_after_speech: bool = False   # open a Whisper reply window after reactive speech


class SentinelStatus(BaseModel):
    enabled: bool
    running: bool
    poll_interval_seconds: float
    dry_run: bool
    allow_motion: bool
    listen_after_speech: bool = False
    polls: int
    events_fired: int
    last_event: str | None = None
    last_event_ts: float | None = None
    cooldown_remaining: dict[str, float] = Field(default_factory=dict)
    last_error: str | None = None


class ListenerStatus(BaseModel):
    enabled: bool
    auto_route: bool
    execute: bool
    dry_run: bool
    audio_enabled: bool
    audio_connected: bool
    audio_static_signal: bool
    vad_backend: str
    stt_model: str
    stt_loaded: bool
    frames_seen: int
    chunks_seen: int
    utterances_seen: int
    transcripts_seen: int
    pending_transcripts: int
    last_transcript: dict | None = None
    last_error: str | None = None
    muted_until: float | None = None