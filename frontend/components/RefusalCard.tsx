export default function RefusalCard({ message, offerOutside, onOutside }: { message: string; offerOutside: boolean; onOutside: () => void }) {
  return (
    <div className="space-y-2 rounded border border-amber-300 bg-amber-50 p-3 text-sm">
      <p className="font-medium text-amber-900">{message}</p>
      {offerOutside && (
        <button type="button" onClick={onOutside} className="rounded border border-amber-400 bg-white px-2 py-1 text-amber-900 hover:bg-amber-100">
          Answer from general knowledge
        </button>
      )}
    </div>
  );
}
