/// Registers the /uberapp WSGI Web Application that exposes production/wsgi/app.py
/// (the SPECS-001 frontend + POST /api/uber-route/recommend endpoint) over real HTTP.
///
/// Setting `Type=2` plus the `WSGI*` properties (as this class originally did) is
/// NOT enough to make the app dispatch as WSGI -- `DispatchClass` must be set to
/// `%SYS.Python.WSGI` explicitly. The Management Portal's "Create Web Application"
/// form does this implicitly when you select its "WSGI [Experimental]" radio
/// button, but that write happens in the portal's own save handler, not as a side
/// effect of any Security.Applications property you can see and copy from the
/// SQL projection. Forgetting it does not error -- the app saves fine and even
/// shows up in the app list -- it just silently falls back to the default
/// (CSP/Zen) dispatcher, which 404s on every path since there's no matching CSP
/// page. Confirmed by editing a broken record in the portal, flipping to the
/// WSGI radio, and observing `DispatchClass` change to `%SYS.Python.WSGI` only
/// then (`CSPZENEnabled` looked like the discriminator from the UI's radio
/// grouping, but editing it directly had no effect -- `DispatchClass` is what
/// actually matters, and research.md documents that dead end too).
///
/// AutheEnabled must be Password (32) *only*, not Unauthenticated (64) at all --
/// even alongside Password. Unauthenticated being one of the allowed methods
/// makes IRIS skip the `401 + WWW-Authenticate` challenge a browser needs to pop
/// its native login dialog, so a plain browser navigation gets a bare, silent
/// rejection instead of a login prompt (`curl -u` masked this earlier by sending
/// Basic Auth pre-emptively, without waiting for a challenge).
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
    ; The actual WSGI-vs-CSP/Zen discriminator -- see class comment.
    Set app.DispatchClass = "%SYS.Python.WSGI"
    Set app.WSGIAppLocation = ..#WSGIAppLocation
    Set app.WSGIAppName = "production.wsgi.app"
    Set app.WSGICallable = "application"
    Set app.WSGIDebug = 0
    Set app.CSRFToken = 0
    Set app.UseCookies = 0
    ; Password(32) only -- see class comment for why Unauthenticated(64) breaks
    ; browser login even when added alongside Password.
    Set app.AutheEnabled = 32
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
