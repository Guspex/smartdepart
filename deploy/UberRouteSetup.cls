/// Registers the /uberapp WSGI Web Application that exposes production/wsgi/app.py
/// (the SPECS-001 frontend + POST /api/uber-route/recommend endpoint) over real HTTP.
///
/// The Management Portal's "Create Web Application" form can create this same
/// configuration, but its "Unauthenticated" auth mode does not actually grant
/// access for WSGI-type applications on this build (IRIS Community 2025.3,
/// %SYS.Python.WSGI is labeled "[Experimental]") -- unauthenticated GET/POST
/// requests get a bare 403 before the WSGI callable is ever invoked (verified:
/// %SYS.Python.WSGI.ImportWSGIApplication() succeeds when called directly, so
/// the rejection happens in IRIS's request auth layer, not in module loading).
/// research.md documents this; the workaround here is to also allow Password
/// auth, so the app is reachable with valid IRIS credentials (e.g. SuperUser).
///
/// Usage (from %SYS namespace, after `production.wsgi.app` has been copied to
/// the path in WSGIAppLocation below):
///   do ##class(UberRoute.Setup).CreateWebApp()
Class UberRoute.Setup Extends %RegisteredObject
{

/// Path on disk (inside the IRIS container) where production/wsgi/app.py lives.
Parameter WSGIAppLocation = "/tmp/uberroute_app";

ClassMethod CreateWebApp() As %Status
{
    Set app = ##class(Security.Applications).%New()
    Set app.Name = "/uberapp"
    Set app.NameSpace = "USER"
    Set app.Enabled = 1
    Set app.Type = 2
    Set app.WSGIAppLocation = ..#WSGIAppLocation
    Set app.WSGIAppName = "production.wsgi.app"
    Set app.WSGICallable = "application"
    Set app.WSGIDebug = 0
    Set app.CSRFToken = 0
    Set app.UseCookies = 0
    ; Unauthenticated(64) alone 403s for WSGI apps on this build (see class
    ; comment) -- Password(32) is included so the app is actually reachable.
    Set app.AutheEnabled = 64 + 32
    Set app.Description = "SPECS-001 Uber Route and Coffee Recommendation Agent frontend"
    Set sc = app.%Save()
    If $$$ISERR(sc) {
        Write "CREATE FAILED: ", $System.Status.GetErrorText(sc), !
    } Else {
        Write "CREATE OK", !
    }
    Quit sc
}

ClassMethod DeleteWebApp() As %Status
{
    Set sc = ##class(Security.Applications).Delete("/uberapp")
    If $$$ISERR(sc) {
        Write "DELETE FAILED: ", $System.Status.GetErrorText(sc), !
    } Else {
        Write "DELETE OK", !
    }
    Quit sc
}

}
