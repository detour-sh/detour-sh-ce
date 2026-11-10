// A small history-API router. The server sends index.html for every app path.
import { createContext, useCallback, useContext, useEffect, useState } from "react";

const RouterContext = createContext({ path: "/", navigate: () => {} });

export function RouterProvider({ children }) {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback((to) => {
    if (to !== window.location.pathname) {
      window.history.pushState({}, "", to);
      setPath(to);
      window.scrollTo(0, 0);
    }
  }, []);
  return <RouterContext.Provider value={{ path, navigate }}>{children}</RouterContext.Provider>;
}

export function useRouter() {
  return useContext(RouterContext);
}

/** Click handler for an <a href> so in-app links don't reload the page. */
export function useLinkHandler() {
  const { navigate } = useRouter();
  return useCallback(
    (event) => {
      const href = event.currentTarget.getAttribute("href");
      if (!href || !href.startsWith("/") || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
      event.preventDefault();
      navigate(href);
    },
    [navigate],
  );
}

export function AppLink({ href, children, ...rest }) {
  const onClick = useLinkHandler();
  return (
    <a href={href} onClick={onClick} {...rest}>
      {children}
    </a>
  );
}
