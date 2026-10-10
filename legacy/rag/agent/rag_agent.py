from typing import Any, Dict, Optional
from agent.retrieval_tool import DomainRetrieverTool
from agent.ollama_client import OllamaLLMClient


SYSTEM_PROMPT = """Eres un asistente de investigación y memoria inteligente.
Tu objetivo es responder con precisión a la pregunta del usuario utilizando EXCLUSIVAMENTE el contexto recuperado proporcionado.
Reglas:
1. Sé conciso, directo y riguroso.
2. Si la información no está en el contexto recuperado, indícalo claramente sin inventar datos.
3. Menciona las fuentes y metadatos relevantes (autor, archivo, fecha, etc.) cuando aporten valor a la respuesta."""


class DomainRAGAgent:
    """
    Agente que orquesta la recuperación contextual híbrida desde un dominio de Qdrant
    y sintetiza la respuesta final mediante el LLM en Ollama.
    """

    def __init__(
        self,
        retriever_tool: DomainRetrieverTool,
        llm_client: Optional[OllamaLLMClient] = None,
    ):
        self.retriever = retriever_tool
        self.llm = llm_client or OllamaLLMClient()

    def ask(
        self,
        question: str,
        domain_collection: str,
        metadata_filter: Optional[Dict[str, Any]] = None,
        limit: int = 3,
    ) -> Dict[str, Any]:
        """
        Ejecuta el ciclo de RAG:
        1. Recuperación híbrida en la colección de dominio elegida.
        2. Construcción de prompt con contexto y metadatos.
        3. Síntesis y respuesta del modelo LLM.
        """
        # 1. Recuperar contexto y resultados
        raw_results = self.retriever.search(
            query=question,
            collection_name=domain_collection,
            metadata_filter=metadata_filter,
            limit=limit,
        )

        formatted_context = self.retriever.retrieve_formatted_context(
            query=question,
            collection_name=domain_collection,
            metadata_filter=metadata_filter,
            limit=limit,
        )

        # 2. Ensamblar prompt aumentado
        user_prompt = (
            f"Pregunta del usuario: {question}\n\n"
            f"Contexto recuperado del dominio '{domain_collection}':\n"
            f"{formatted_context}\n\n"
            f"Instrucción: Responde a la pregunta basándote en el contexto anterior."
        )

        # 3. Invocar al modelo
        answer = self.llm.generate(
            prompt=user_prompt,
            system_prompt=SYSTEM_PROMPT,
        )

        return {
            "question": question,
            "domain_collection": domain_collection,
            "answer": answer,
            "sources": [
                {
                    "chunk_id": r.chunk_id,
                    "score_rrf": round(r.score, 4),
                    "metadata": r.metadata,
                    "text_snippet": r.text[:120] + "..." if len(r.text) > 120 else r.text,
                }
                for r in raw_results
            ],
        }
