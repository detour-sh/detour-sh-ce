import { createContext, useCallback, useContext, useEffect, useState } from "react";
import {
  Header,
  HeaderContainer,
  HeaderGlobalAction,
  HeaderGlobalBar,
  HeaderMenuButton,
  HeaderName,
  SideNav,
  SideNavDivider,
  SideNavItems,
  SideNavLink,
  SkipToContent,
  Tag,
} from "@carbon/react";
import { Activity, CheckmarkOutline, Connect, Flow, Logout, Settings, TaskApproved, UserAvatar } from "@carbon/icons-react";
import { api } from "./api.js";
import { useRouter } from "./router.jsx";
import Review from "./pages/Review.jsx";
import RoutesPage from "./pages/Routes.jsx";
import RouteDetail from "./pages/RouteDetail.jsx";
import ActivityPage from "./pages/Activity.jsx";
import ChecksPage from "./pages/Checks.jsx";
import Connections from "./pages/Connections.jsx";
import SettingsPage from "./pages/Settings.jsx";
import NotFound from "./pages/NotFound.jsx";
import SignIn from "./pages/SignIn.jsx";

const SessionContext = createContext({ session: null, refreshSession: () => {}, signOut: () => {} });

export function useSession() {
  return useContext(SessionContext);
}

export function usePageTitle(title) {
  useEffect(() => {
    document.title = title ? `${title} · Detour` : "Detour";
  }, [title]);
}

const NAV = [
  { href: "/", label: "Review", icon: TaskApproved, match: (p) => p === "/" || p === "/review" },
  { href: "/routes", label: "Routes", icon: Flow, match: (p) => p.startsWith("/routes") },
  { href: "/checks", label: "Check runs", icon: CheckmarkOutline, match: (p) => p === "/checks" },
  { href: "/activity", label: "Activity", icon: Activity, match: (p) => p === "/activity" },
];
const ACCESS_NAV = [
  { href: "/connections", label: "Agent connections", icon: Connect, match: (p) => p === "/connections" },
  { href: "/settings", label: "Settings", icon: Settings, match: (p) => p === "/settings" },
];

function page(path) {
  if (path === "/" || path === "/review") return <Review />;
  if (path === "/routes") return <RoutesPage />;
  const routeMatch = path.match(/^\/routes\/([A-Za-z0-9_-]+)$/);
  // Keyed so moving between routes starts fresh; an open editor can't save into the route you left.
  if (routeMatch) return <RouteDetail key={routeMatch[1]} routeId={routeMatch[1]} />;
  if (path === "/checks") return <ChecksPage />;
  if (path === "/activity") return <ActivityPage />;
  if (path === "/connections") return <Connections />;
  if (path === "/settings") return <SettingsPage />;
  return <NotFound />;
}

export default function App() {
  const [auth, setAuth] = useState(null);
  const [authFailed, setAuthFailed] = useState(false);
  const refreshAuth = useCallback(() => {
    api.authStatus().then((status) => { setAuth(status); setAuthFailed(false); }).catch(() => setAuthFailed(true));
  }, []);
  useEffect(refreshAuth, [refreshAuth]);
  useEffect(() => {
    window.addEventListener("detour:signed-out", refreshAuth);
    return () => window.removeEventListener("detour:signed-out", refreshAuth);
  }, [refreshAuth]);

  if (authFailed) {
    return (
      <main className="dt-auth" id="main-content">
        <p>Detour isn't answering. Check that it is running, then reload this page.</p>
      </main>
    );
  }
  if (!auth) return null;
  if (!auth.signed_in) return <SignIn status={auth} onDone={refreshAuth} />;
  return <Signed auth={auth} refreshAuth={refreshAuth} />;
}

function Signed({ auth, refreshAuth }) {
  const [session, setSession] = useState(null);
  const refreshSession = useCallback(() => {
    api.session().then(setSession).catch(() => {});
  }, []);
  useEffect(refreshSession, [refreshSession]);
  const signOut = useCallback(() => {
    api.signOut().finally(refreshAuth);
  }, [refreshAuth]);

  return (
    <SessionContext.Provider value={{ session, refreshSession, signOut, hosted: auth.hosted, userName: auth.user_name }}>
      <HeaderContainer render={Shell} />
    </SessionContext.Provider>
  );
}

// HeaderContainer renders `render` as a component, so it must be a stable, module-level one.
// An inline function would be a new component type on every render and remount every page.
function Shell({ isSideNavExpanded, onClickSideNavExpand }) {
  const { path, navigate } = useRouter();
  const { session, hosted, signOut, userName } = useSession();
  const go = (event, href) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    navigate(href);
    if (isSideNavExpanded && window.innerWidth < 1056) onClickSideNavExpand();
  };
  const link = (item) => (
    <SideNavLink
      key={item.href}
      href={item.href}
      renderIcon={item.icon}
      isActive={item.match(path)}
      aria-current={item.match(path) ? "page" : undefined}
      onClick={(event) => go(event, item.href)}
    >
      {item.label}
      {item.href === "/" && session?.pending_count > 0 ? (
        <Tag size="sm" type="blue" className="dt-nav-count">
          {session.pending_count}
        </Tag>
      ) : null}
    </SideNavLink>
  );
  return (
    <>
      <Header aria-label="Detour">
        <SkipToContent href="#main-content" />
        <HeaderMenuButton
          aria-label={isSideNavExpanded ? "Close menu" : "Open menu"}
          onClick={onClickSideNavExpand}
          isActive={isSideNavExpanded}
          aria-expanded={isSideNavExpanded}
        />
        <HeaderName href="/" prefix="" onClick={(event) => go(event, "/")}>
          Detour
        </HeaderName>
        <HeaderGlobalBar>
          {hosted ? (
            <Tag type="blue" size="sm" className="dt-header-tag" title="Detour is hosted and requires sign-in">
              Hosted
            </Tag>
          ) : (
            <Tag type="cool-gray" size="sm" className="dt-header-tag" title="Detour only listens on this computer">
              Local only
            </Tag>
          )}
          <HeaderGlobalAction
            aria-label={session ? `${session.user_name}: settings` : "Settings"}
            tooltipAlignment="end"
            onClick={() => navigate("/settings")}
          >
            <UserAvatar size={20} />
          </HeaderGlobalAction>
          {hosted ? (
            <HeaderGlobalAction aria-label={`Sign out ${userName || ""}`.trim()} tooltipAlignment="end" onClick={signOut}>
              <Logout size={20} />
            </HeaderGlobalAction>
          ) : null}
        </HeaderGlobalBar>
        <SideNav aria-label="Primary" expanded={isSideNavExpanded} onSideNavBlur={onClickSideNavExpand} href="#main-content">
          <SideNavItems>
            {NAV.map(link)}
            <SideNavDivider />
            {ACCESS_NAV.map(link)}
          </SideNavItems>
        </SideNav>
      </Header>
      <main id="main-content" className="dt-content" tabIndex={-1}>
        {page(path)}
      </main>
    </>
  );
}
