import { useRef, useState } from 'react'
import { motion } from 'framer-motion'
import type { DocumentResult } from '../../types/swarm'

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export function ExecutiveSummaryCard({ document }: { document: DocumentResult }) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)

  const toggleAudio = () => {
    if (!audioRef.current) return
    if (playing) {
      audioRef.current.pause()
      setPlaying(false)
    } else {
      audioRef.current.play()
      setPlaying(true)
    }
  }

  const audioSrc = document.tts_audio_url
    ? `${API_BASE}/audio/${document.tts_audio_url.split('/').pop()}`
    : null

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
      className="rounded-xl border border-green-800/60 bg-gradient-to-br from-green-950/40 to-gray-950 p-5"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <div className="text-[10px] text-green-400 uppercase tracking-widest font-semibold mb-1">
            Intelligence Report
          </div>
          <h2 className="text-lg font-bold text-white mb-2">{document.title}</h2>
          <p className="text-sm text-gray-300 leading-relaxed">{document.executive_summary}</p>
        </div>

        {audioSrc && (
          <div className="shrink-0">
            <button
              onClick={toggleAudio}
              className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border transition-colors ${
                playing
                  ? 'border-green-600 bg-green-900/40 text-green-300'
                  : 'border-gray-700 bg-gray-800/50 text-gray-400 hover:text-gray-200'
              }`}
            >
              {playing ? '⏸ Pause' : '▶ Listen'}
            </button>
            <audio
              ref={audioRef}
              src={audioSrc}
              onEnded={() => setPlaying(false)}
            />
          </div>
        )}
      </div>
    </motion.div>
  )
}
