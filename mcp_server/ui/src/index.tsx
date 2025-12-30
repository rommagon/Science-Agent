import React from 'react';
import { createRoot } from 'react-dom/client';
import MustReadsWidget from './MustReadsWidget';

// Export the widget component for OpenAI Apps SDK
export { MustReadsWidget };

// Auto-render if data is provided via data attribute
if (typeof document !== 'undefined') {
  const container = document.getElementById('must-reads-widget-root');
  if (container) {
    const dataAttr = container.getAttribute('data-must-reads');
    if (dataAttr) {
      try {
        const data = JSON.parse(dataAttr);
        const root = createRoot(container);
        root.render(<MustReadsWidget data={data} />);
      } catch (e) {
        console.error('Failed to parse must-reads data:', e);
      }
    }
  }
}
