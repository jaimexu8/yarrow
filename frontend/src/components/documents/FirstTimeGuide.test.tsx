import { afterEach, expect, test, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { uploadSampleDocument } from '@/lib/sampleDocument';
import { FirstTimeGuide } from './FirstTimeGuide';

vi.mock('@/lib/sampleDocument', () => ({
  uploadSampleDocument: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test('explains how to get started in three steps', () => {
  render(<FirstTimeGuide onSampleUploaded={vi.fn()} />);

  expect(
    screen.getByRole('heading', { name: 'Get started with Yarrow' })
  ).toBeTruthy();
  const steps = screen.getAllByRole('listitem');
  expect(steps).toHaveLength(3);
  expect(steps[0].textContent).toContain('1. Upload');
  expect(steps[1].textContent).toContain('2. Processing');
  expect(steps[2].textContent).toContain('3. Review and export');
});

test('the actions are a labeled link and button, reachable by keyboard', () => {
  render(<FirstTimeGuide onSampleUploaded={vi.fn()} />);

  const upload = screen.getByRole('link', { name: 'Upload documents' });
  expect(upload.getAttribute('href')).toBe('/upload');
  const sample = screen.getByRole('button', { name: 'Try sample document' });
  // Native controls with no tabindex override stay in the tab order.
  expect(upload.hasAttribute('tabindex')).toBe(false);
  expect(sample.hasAttribute('tabindex')).toBe(false);
  expect((sample as HTMLButtonElement).disabled).toBe(false);
  // The button points at the text saying what the sample is.
  const hint = document.getElementById(
    sample.getAttribute('aria-describedby') ?? ''
  );
  expect(hint?.textContent).toContain('made-up company');
  // The guide is a named region for screen readers.
  expect(
    screen.getByRole('region', { name: 'Get started with Yarrow' })
  ).toBeTruthy();
});

test('Try sample document uploads the sample and tells the library', async () => {
  vi.mocked(uploadSampleDocument).mockResolvedValueOnce({
    kind: 'accepted',
    documentId: 'doc-1',
    processingStarted: true,
  });
  const onSampleUploaded = vi.fn();
  render(<FirstTimeGuide onSampleUploaded={onSampleUploaded} />);

  fireEvent.click(screen.getByRole('button', { name: 'Try sample document' }));

  // Busy while it uploads, so it cannot be sent twice.
  const busy = screen.getByRole('button', { name: /Adding sample document/ });
  expect((busy as HTMLButtonElement).disabled).toBe(true);
  expect(busy.getAttribute('aria-busy')).toBe('true');
  await waitFor(() => expect(onSampleUploaded).toHaveBeenCalledTimes(1));
  expect(onSampleUploaded).toHaveBeenCalledWith({
    processingStarted: true,
    message: undefined,
  });
  expect(uploadSampleDocument).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('alert')).toBeNull();
});

test('a failed sample upload is explained and can be retried', async () => {
  vi.mocked(uploadSampleDocument).mockResolvedValueOnce({
    kind: 'failed',
    message: 'The sample document could not be loaded. Please try again.',
  });
  const onSampleUploaded = vi.fn();
  render(<FirstTimeGuide onSampleUploaded={onSampleUploaded} />);

  fireEvent.click(screen.getByRole('button', { name: 'Try sample document' }));

  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toContain('could not be loaded');
  expect(onSampleUploaded).not.toHaveBeenCalled();
  const retry = screen.getByRole('button', { name: 'Try sample document' });
  expect((retry as HTMLButtonElement).disabled).toBe(false);
});

test('a rejected sample upload shows the reason from the server', async () => {
  vi.mocked(uploadSampleDocument).mockResolvedValueOnce({
    kind: 'rejected',
    reason: 'You have reached your storage limit.',
  });
  render(<FirstTimeGuide onSampleUploaded={vi.fn()} />);

  fireEvent.click(screen.getByRole('button', { name: 'Try sample document' }));

  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toBe(
    'The sample document was not added. You have reached your storage limit.'
  );
});

test('a sample that was stored but not queued passes the reason on', async () => {
  vi.mocked(uploadSampleDocument).mockResolvedValueOnce({
    kind: 'accepted',
    documentId: 'doc-1',
    processingStarted: false,
    message: 'Processing could not be started. Use Reprocess to try again.',
  });
  const onSampleUploaded = vi.fn();
  render(<FirstTimeGuide onSampleUploaded={onSampleUploaded} />);

  fireEvent.click(screen.getByRole('button', { name: 'Try sample document' }));

  await waitFor(() =>
    expect(onSampleUploaded).toHaveBeenCalledWith({
      processingStarted: false,
      message: 'Processing could not be started. Use Reprocess to try again.',
    })
  );
});
