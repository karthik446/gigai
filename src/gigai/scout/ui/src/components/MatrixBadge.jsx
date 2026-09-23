export default function MatrixBadge({ status }) {
  return <span className={`status-badge ${status}`}>{status}</span>;
}
