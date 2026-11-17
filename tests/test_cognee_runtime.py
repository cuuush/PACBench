from pathlib import Path

from provider_adapters.cognee_runtime import plan_config


def test_cognee_native_model_and_embedding_routes_are_explicit():
    model='deepseek/deepseek-v4.1-flash-1017'
    result=plan_config(Path('/offline/cognee'),model,'https://openrouter.ai/api/v1','inert')
    assert result['LLM_PROVIDER']=='openai'
    assert result['LLM_MODEL']=='openai/'+model  # LiteLLM's provider prefix, stripped before the wire.
    assert result['EMBEDDING_ENDPOINT']=='http://127.0.0.1:11435/api/embed'
    assert result['EMBEDDING_PROVIDER']=='ollama'
    assert result['DATA_ROOT_DIRECTORY']=='/offline/cognee/cognee-data'
    assert result['SYSTEM_ROOT_DIRECTORY']=='/offline/cognee/cognee-system'
