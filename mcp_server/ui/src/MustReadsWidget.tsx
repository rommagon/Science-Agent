import React, { useState, useEffect } from 'react';

// TypeScript interfaces
interface MustRead {
  id: string;
  title: string;
  published_date: string;
  source: string;
  venue: string;
  url: string;
  why_it_matters: string;
  key_findings: string[];
  rank_score: number;
  rank_reason: string;
}

interface MustReadsData {
  must_reads: MustRead[];
  generated_at: string;
  window_days: number;
  total_candidates: number;
}

interface MustReadsWidgetProps {
  data: MustReadsData;
}

// OpenAI Apps SDK interface (available in window.openai)
declare global {
  interface Window {
    openai: {
      openExternal: (params: { href: string }) => void;
      sendFollowUpMessage: (params: { prompt: string }) => void;
      callTool: (toolName: string, params: any) => void;
      setWidgetState: (state: any) => void;
      getWidgetState: () => any;
    };
  }
}

const MustReadsWidget: React.FC<MustReadsWidgetProps> = ({ data }) => {
  const [savedIds, setSavedIds] = useState<Set<string>>(new Set());
  const [isLoading, setIsLoading] = useState(false);

  // Load saved state on mount
  useEffect(() => {
    const loadSavedState = async () => {
      try {
        const state = await window.openai.getWidgetState();
        if (state?.savedIds) {
          setSavedIds(new Set(state.savedIds));
        }
      } catch (e) {
        console.error('Failed to load saved state:', e);
      }
    };
    loadSavedState();
  }, []);

  const handleOpen = (url: string) => {
    window.openai.openExternal({ href: url });
  };

  const handleExplainWhy = (title: string, whyItMatters: string) => {
    const prompt = `Can you explain in more detail why "${title}" is important? Context: ${whyItMatters}`;
    window.openai.sendFollowUpMessage({ prompt });
  };

  const handleRefresh = async () => {
    setIsLoading(true);
    try {
      await window.openai.callTool('get_must_reads', {
        since_days: data.window_days,
        limit: data.must_reads.length,
      });
    } catch (e) {
      console.error('Failed to refresh:', e);
    } finally {
      setIsLoading(false);
    }
  };

  const handleToggleSave = async (id: string) => {
    const newSavedIds = new Set(savedIds);
    if (newSavedIds.has(id)) {
      newSavedIds.delete(id);
    } else {
      newSavedIds.add(id);
    }
    setSavedIds(newSavedIds);

    // Persist to widget state (minimal payload)
    try {
      await window.openai.setWidgetState({
        savedIds: Array.from(newSavedIds),
      });
    } catch (e) {
      console.error('Failed to save state:', e);
    }
  };

  const formatDate = (isoDate: string): string => {
    try {
      const date = new Date(isoDate);
      return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      });
    } catch {
      return isoDate;
    }
  };

  if (data.must_reads.length === 0) {
    return (
      <div style={styles.container}>
        <div style={styles.header}>
          <h2 style={styles.title}>Must Reads</h2>
          <button
            onClick={handleRefresh}
            disabled={isLoading}
            style={styles.refreshButton}
          >
            {isLoading ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
        <div style={styles.emptyState}>
          <p>No must-reads found for the last {data.window_days} days.</p>
          <p style={styles.emptySubtext}>
            Try refreshing or extending the time window.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <div>
          <h2 style={styles.title}>Must Reads</h2>
          <p style={styles.subtitle}>
            {data.must_reads.length} of {data.total_candidates} publications
            from the last {data.window_days} days
          </p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={isLoading}
          style={styles.refreshButton}
        >
          {isLoading ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      <div style={styles.cardGrid}>
        {data.must_reads.map((item) => (
          <div key={item.id} style={styles.card}>
            <div style={styles.cardHeader}>
              <div style={styles.cardMeta}>
                <span style={styles.source}>{item.source}</span>
                {item.venue && <span style={styles.venue}> • {item.venue}</span>}
                <span style={styles.date}> • {formatDate(item.published_date)}</span>
              </div>
              <button
                onClick={() => handleToggleSave(item.id)}
                style={{
                  ...styles.saveButton,
                  ...(savedIds.has(item.id) ? styles.saveButtonActive : {}),
                }}
                title={savedIds.has(item.id) ? 'Unsave' : 'Save'}
              >
                {savedIds.has(item.id) ? '★' : '☆'}
              </button>
            </div>

            <h3 style={styles.cardTitle}>{item.title}</h3>

            <div style={styles.whyItMatters}>
              <strong>Why it matters:</strong> {item.why_it_matters}
            </div>

            {item.key_findings.length > 0 && (
              <div style={styles.keyFindings}>
                <strong>Key findings:</strong>
                <ul style={styles.findingsList}>
                  {item.key_findings.map((finding, idx) => (
                    <li key={idx} style={styles.findingItem}>
                      {finding}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div style={styles.rankInfo}>
              <span style={styles.rankScore}>
                Score: {Math.round(item.rank_score)}
              </span>
              <span style={styles.rankReason}>{item.rank_reason}</span>
            </div>

            <div style={styles.cardActions}>
              <button
                onClick={() => handleOpen(item.url)}
                style={styles.primaryButton}
              >
                Open
              </button>
              <button
                onClick={() => handleExplainWhy(item.title, item.why_it_matters)}
                style={styles.secondaryButton}
              >
                Explain why
              </button>
            </div>
          </div>
        ))}
      </div>

      <div style={styles.footer}>
        <small style={styles.footerText}>
          Generated at {new Date(data.generated_at).toLocaleString()}
        </small>
      </div>
    </div>
  );
};

// Styles
const styles = {
  container: {
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    padding: '20px',
    maxWidth: '1200px',
    margin: '0 auto',
  } as React.CSSProperties,
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: '24px',
    borderBottom: '2px solid #e5e7eb',
    paddingBottom: '16px',
  } as React.CSSProperties,
  title: {
    fontSize: '28px',
    fontWeight: 'bold',
    margin: '0 0 8px 0',
    color: '#111827',
  } as React.CSSProperties,
  subtitle: {
    fontSize: '14px',
    color: '#6b7280',
    margin: 0,
  } as React.CSSProperties,
  refreshButton: {
    padding: '8px 16px',
    fontSize: '14px',
    fontWeight: '500',
    color: '#fff',
    backgroundColor: '#3b82f6',
    border: 'none',
    borderRadius: '6px',
    cursor: 'pointer',
    transition: 'background-color 0.2s',
  } as React.CSSProperties,
  cardGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(400px, 1fr))',
    gap: '20px',
    marginBottom: '24px',
  } as React.CSSProperties,
  card: {
    backgroundColor: '#fff',
    border: '1px solid #e5e7eb',
    borderRadius: '8px',
    padding: '20px',
    boxShadow: '0 1px 3px rgba(0, 0, 0, 0.1)',
    transition: 'box-shadow 0.2s',
  } as React.CSSProperties,
  cardHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: '12px',
  } as React.CSSProperties,
  cardMeta: {
    fontSize: '12px',
    color: '#6b7280',
  } as React.CSSProperties,
  source: {
    fontWeight: '600',
    color: '#4b5563',
  } as React.CSSProperties,
  venue: {
    color: '#6b7280',
  } as React.CSSProperties,
  date: {
    color: '#9ca3af',
  } as React.CSSProperties,
  saveButton: {
    background: 'none',
    border: 'none',
    fontSize: '20px',
    cursor: 'pointer',
    color: '#d1d5db',
    padding: '0 4px',
    transition: 'color 0.2s',
  } as React.CSSProperties,
  saveButtonActive: {
    color: '#f59e0b',
  } as React.CSSProperties,
  cardTitle: {
    fontSize: '16px',
    fontWeight: '600',
    color: '#111827',
    margin: '0 0 12px 0',
    lineHeight: '1.5',
  } as React.CSSProperties,
  whyItMatters: {
    fontSize: '14px',
    color: '#374151',
    marginBottom: '12px',
    padding: '12px',
    backgroundColor: '#f9fafb',
    borderRadius: '4px',
    borderLeft: '3px solid #3b82f6',
  } as React.CSSProperties,
  keyFindings: {
    fontSize: '13px',
    color: '#4b5563',
    marginBottom: '12px',
  } as React.CSSProperties,
  findingsList: {
    margin: '8px 0',
    paddingLeft: '20px',
  } as React.CSSProperties,
  findingItem: {
    marginBottom: '4px',
  } as React.CSSProperties,
  rankInfo: {
    fontSize: '11px',
    color: '#9ca3af',
    marginBottom: '16px',
    display: 'flex',
    gap: '8px',
    alignItems: 'center',
  } as React.CSSProperties,
  rankScore: {
    fontWeight: '600',
    color: '#6b7280',
  } as React.CSSProperties,
  rankReason: {
    fontStyle: 'italic',
  } as React.CSSProperties,
  cardActions: {
    display: 'flex',
    gap: '8px',
  } as React.CSSProperties,
  primaryButton: {
    flex: 1,
    padding: '8px 16px',
    fontSize: '14px',
    fontWeight: '500',
    color: '#fff',
    backgroundColor: '#10b981',
    border: 'none',
    borderRadius: '6px',
    cursor: 'pointer',
    transition: 'background-color 0.2s',
  } as React.CSSProperties,
  secondaryButton: {
    flex: 1,
    padding: '8px 16px',
    fontSize: '14px',
    fontWeight: '500',
    color: '#4b5563',
    backgroundColor: '#f3f4f6',
    border: '1px solid #d1d5db',
    borderRadius: '6px',
    cursor: 'pointer',
    transition: 'background-color 0.2s',
  } as React.CSSProperties,
  footer: {
    borderTop: '1px solid #e5e7eb',
    paddingTop: '16px',
    textAlign: 'center' as const,
  } as React.CSSProperties,
  footerText: {
    color: '#9ca3af',
  } as React.CSSProperties,
  emptyState: {
    textAlign: 'center' as const,
    padding: '60px 20px',
    color: '#6b7280',
  } as React.CSSProperties,
  emptySubtext: {
    fontSize: '14px',
    color: '#9ca3af',
    marginTop: '8px',
  } as React.CSSProperties,
};

export default MustReadsWidget;
