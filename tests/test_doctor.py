from ai_digest.config import RuntimeConfig
from ai_digest.doctor import codex_profiles


def test_doctor_probes_active_phase2_model_and_actual_reasoning():
    runtime = RuntimeConfig()
    runtime.codex.phase2_label_reasoning = "medium"
    runtime.codex.router_model = "unused-legacy-router"
    profiles = codex_profiles(runtime)
    assert ("gpt-5.6-luna", "medium") in profiles
    assert (runtime.codex.research_model, runtime.codex.research_reasoning) in profiles
    assert all(model != "unused-legacy-router" for model, _ in profiles)
    runtime.codex.phase2_engine = "attention_editor_v3"
    assert ("unused-legacy-router", runtime.codex.router_reasoning) in codex_profiles(runtime)
