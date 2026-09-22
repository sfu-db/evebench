from fastapi import Request

from evebench.application import CheckService
from evebench.benchmark import ResultStore
from evebench.generator import GenerationService


def get_check_service(request: Request) -> CheckService:
    return request.app.state.check_service


def get_result_store(request: Request) -> ResultStore:
    return request.app.state.result_store


def get_generation_service(request: Request) -> GenerationService:
    return request.app.state.generation_service
