"""HTTP API for the Context Layer."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import Principal, get_principal
from app.context_orchestrator import context_orchestrator
from app.llm import llm_service
from app.memory_orchestrator import application_orchestrator
from app.models import QueryRequest, QueryResponse
from app.observability import metrics_response
from app.prompt_builder import prompt_builder
from app.router import query_router
from app.services.safety_policy import safety_policy
from app.routing_trace import content_details, log_routing_event
from app.performance import timed

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
def root():
    return {"message": "Context Layer Running"}


@router.get("/health")
def health():
    """Liveness: the API process can accept requests."""
    return {"status": "alive"}


@router.get("/ready")
def ready():
    """Readiness: all answer-path dependencies are reachable."""
    service_status = {"status": "healthy", "neo4j": True, "chromadb": True, "groq": True, "redis": True}
    def neo4j_check():
        from app.storage.neo4j import neo4j
        with neo4j.get_session() as session:
            session.run("RETURN 1").consume()
    checks = {
        "neo4j": neo4j_check,
        "chromadb": lambda: __import__("app.storage.chroma", fromlist=["chroma"]).chroma.get_collection().count(),
        # Keep the existing response key for compatibility while checking the
        # configured primary provider through the LLM boundary.
        "groq": llm_service.check_ready,
        "redis": lambda: __import__("app.services.jobs", fromlist=["job_queue"]).job_queue.client.ping(),
    }
    for name, check in checks.items():
        try:
            check()
        except Exception:
            service_status[name] = False
            service_status["status"] = "degraded"
    return service_status


@router.get("/metrics", include_in_schema=False)
def metrics():
    return metrics_response()


@router.get("/demo/session")
def demo_session(principal: Principal = Depends(get_principal)):
    return {"user_id": principal.user_id}


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest, principal: Principal = Depends(get_principal)):
    """Answer a question using the identity authenticated at the API boundary."""
    if request.user_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The request user_id does not match the authenticated demo identity.",
        )
    user_id = principal.user_id
    log_routing_event(
        "request_received",
        authenticated_user=user_id,
        agent_id=request.agent_id,
        query=content_details(request.query),
    )
    refusal = safety_policy.refusal_for(request.query)
    if refusal:
        return QueryResponse(answer=refusal, context_used=[])

    with timed("conversation.user_persist"):
        application_orchestrator.process_message(user_id=user_id, agent_id=request.agent_id, role="user", content=request.query)
    with timed("routing"):
        decision = query_router.analyze(request.query)
    routes = decision.selected_routes
    logger.info("Query routed: user_id=%s routes=%s", user_id, [route.value for route in routes])
    with timed("context.assembly"):
        context = context_orchestrator.build_context(
            user_id=user_id, agent_id=request.agent_id, query=request.query,
            routes=routes, decision=decision,
        )
    with timed("prompt.construction"):
        prompt = prompt_builder.build(query=request.query, context=context)
    with timed("llm.answer"):
        answer = llm_service.generate(prompt, query=request.query)
    with timed("conversation.assistant_persist"):
        application_orchestrator.process_message(user_id=user_id, agent_id=request.agent_id, role="assistant", content=answer)

    context_used = []
    if context.conversation:
        context_used.append("conversation")
    if context.memories:
        context_used.append("memory")
    if context.knowledge:
        context_used.append("knowledge")
    log_routing_event(
        "answer_generated",
        context_used=context_used,
        answer=content_details(answer),
    )
    return QueryResponse(answer=answer, context_used=context_used)
