import { api } from "../api.js";
import { usePageTitle } from "../App.jsx";
import HistoryList from "../components/HistoryList.jsx";
import { ErrorNote, Loading } from "../components/common.jsx";
import { useResource } from "../util.js";

export default function ActivityPage() {
  usePageTitle("Activity");
  const events = useResource(() => api.events(), []);
  return (
    <div className="dt-page">
      <div className="dt-page-header">
        <div>
          <h1>Activity</h1>
          <p>Every change to your routes, by you or by an agent. Undo any change to an item or target that hasn't changed again since.</p>
        </div>
      </div>
      <ErrorNote error={events.error} title="Couldn't load activity" />
      {events.data ? (
        <section className="dt-tile" aria-label="Recent changes">
          <HistoryList events={events.data.events} onChanged={events.reload} showRoute />
        </section>
      ) : (
        <Loading />
      )}
    </div>
  );
}
