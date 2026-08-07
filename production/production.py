"""Declarative Production definition for the Uber Route & Coffee Recommendation Agent.

Loaded into IRIS with the `intersystems_pyprod` CLI (see quickstart.md step 5). Declares
the four PyProd hosts required by constitution Principle I (PyProd-First
Interoperability): one Business Service, one Business Process, two Business Operations
— all pure Python, no ObjectScript-first hosts.

Host class names and item names are underscore-free PascalCase (`BsUberRouteService`, not
`BS_UberRouteService`) — IRIS 2026.1 Build 234U was found to silently truncate brand-new
class names at the first underscore during compilation (research.md §12).
"""
from intersystems_pyprod import OperationItem, Production, ProcessItem, ServiceItem

iris_package_name = "UberRoute"


class UberRouteProduction(Production):
    description = "Uber Route and Coffee Recommendation Agent (SPECS-001)"
    actor_pool_size = 2
    testing_enabled = True
    log_general_trace_events = True

    services = [
        ServiceItem(
            name="BsUberRouteService",
            class_name="UberRoute.BsUberRouteService",
            comment=(
                "Adapterless — fed by production/wsgi/app.py, which calls "
                "director.create_business_service(...).process_input(...) per request "
                "(research.md §2)."
            ),
        ),
    ]

    processes = [
        ProcessItem(
            name="BpRouteOrchestrator",
            class_name="UberRoute.BpRouteOrchestrator",
            pool_size=1,
        ),
    ]

    operations = [
        OperationItem(
            name="BoIntegratedMlPredictor",
            class_name="UberRoute.BoIntegratedMlPredictor",
        ),
        OperationItem(
            name="BoHybridRagEngine",
            class_name="UberRoute.BoHybridRagEngine",
        ),
    ]
