import './Spinner.css'

export default function Spinner({ text = 'Loading...' }: { text?: string }) {
  return (
    <div className="spinner-container">
      <div className="spinner" />
      <p>{text}</p>
    </div>
  )
}
