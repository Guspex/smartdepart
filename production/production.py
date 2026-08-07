"""Declarative Production definition for the Uber Route & Coffee Recommendation Agent.

Loaded into IRIS with the `intersystems_pyprod` CLI (see quickstart.md step 5). Declares
the four PyProd hosts required by constitution Principle I (PyProd-First
Interoperability): one Business Service, one Business Process, two Business Operations
— all pure Python, no ObjectScript-first hosts.
"""
from intersystems_pyprod import OperationItem, Production, ProcessItem, ServiceItem

iris_package_name = "UberRoute"


class UberRouteProduction(Production):
    description = "Uber Route & Coffee Recommendation Agent (SPECS-001)"
    actor_pool_size = 2
    testing_enabled = True
    log_general_trace_events = True

    services = [
        ServiceItem(
            name="BS_UberRouteService",
            class_name="UberRoute.BS_UberRouteService",
            comment=(
                "Adapterless — fed by production/wsgi/app.py, which calls "
                "director.create_business_service(...).process_input(...) per request "
                "(research.md §2)."
            ),
        ),
    ]

    processes = [
        ProcessItem(
            name="BP_RouteOrchestrator",
            class_name="UberRoute.BP_RouteOrchestrator",
            pool_size=1,
        ),
    ]

    operations = [
        OperationItem(
            name="BO_IntegratedMLPredictor",
            class_name="UberRoute.BO_IntegratedMLPredictor",
        ),
        OperationItem(
            name="BO_HybridRAGEngine",
            class_name="UberRoute.BO_HybridRAGEngine",
        ),
    ]
