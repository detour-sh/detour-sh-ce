import { usePageTitle } from "../App.jsx";
import { AppLink } from "../router.jsx";

export default function NotFound() {
  usePageTitle("Not found");
  return (
    <div className="dt-page">
      <div className="dt-empty">
        <h1 className="dt-heading-04">This page doesn't exist</h1>
        <AppLink href="/" className="cds--link">
          Go to Review
        </AppLink>
      </div>
    </div>
  );
}
