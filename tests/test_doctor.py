from ai_digest.config import RuntimeConfig
from ai_digest.doctor import codex_profiles


def test_doctor_probes_active_phase2_model_and_actual_reasoning():
    runtime = RuntimeConfig()
    runtime.codex.phase2_label_reasoning = "medium"
    runtime.codex.router_model = "unused-legacy-router"
    profiles = codex_profiles(runtime)
    assert ("gpt-5.6-luna", "medium") not in profiles  # Jev is not a Codex request.
    assert (runtime.codex.research_model, runtime.codex.research_reasoning) in profiles
    assert all(model != "unused-legacy-router" for model, _ in profiles)
    runtime.codex.phase2_engine = "semantic_labels_v1"
    assert ("gpt-5.6-luna", "medium") in codex_profiles(runtime)
    runtime.codex.phase2_engine = "attention_editor_v3"
    assert ("unused-legacy-router", runtime.codex.router_reasoning) in codex_profiles(runtime)


def test_jev_profiles_never_probe_unused_codex_phase2_models():
    runtime = RuntimeConfig()
    runtime.codex.phase2_subject_keys = True
    runtime.codex.phase2_alias_model = "must-not-call-alias"
    runtime.codex.phase2_label_model = "must-not-call-label"
    for engine in ("jev_reading_v2", "jev_reading_v3"):
        runtime.codex.phase2_engine = engine
        assert all(not model.startswith("must-not-call") for model, _ in codex_profiles(runtime))
