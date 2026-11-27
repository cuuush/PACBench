import json, os, subprocess, sys

def run(script):
    p=subprocess.run([sys.executable,'-c',script],env={**os.environ,'PACBENCH_OPENROUTER_PROFILE':'luna-bedrock-global'},text=True,capture_output=True)
    assert p.returncode==0,p.stderr
    return json.loads(p.stdout)

def test_explicit_luna_profile_has_no_fallback_or_injected_inference_settings():
    d=run("from pacbench.openrouter_route import *; import json; print(json.dumps(with_route({'messages':[]})))")
    assert d['model']=='openai/gpt-6-luna' and d['provider']['only']==['amazon-bedrock/global']
    assert d['provider']['allow_fallbacks'] is False
    assert not {'reasoning','reasoning_effort','max_tokens','max_completion_tokens'}&d.keys()
