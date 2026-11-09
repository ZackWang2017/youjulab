import { describe, expect, it } from 'vitest';
import { bookedOccurredAt, localDateInputValue } from './format';

describe('bookedOccurredAt', () => {
  it('uses the current time when today 15:30 has not arrived', () => {
    const now = new Date(2026, 10, 3, 14, 47, 0);
    expect(bookedOccurredAt(localDateInputValue(now), now)).toBe(now.toISOString());
  });

  it('keeps 15:30 for a past day', () => {
    const now = new Date(2026, 10, 3, 14, 47, 0);
    const booked = new Date(bookedOccurredAt('2026-11-02', now)!);
    expect(booked.getFullYear()).toBe(2026);
    expect(booked.getMonth()).toBe(10);
    expect(booked.getDate()).toBe(2);
    expect(booked.getHours()).toBe(15);
    expect(booked.getMinutes()).toBe(30);
  });

  it('keeps 15:30 today after that time has passed', () => {
    const now = new Date(2026, 10, 3, 16, 0, 0);
    const booked = new Date(bookedOccurredAt('2026-11-03', now)!);
    expect(booked.getHours()).toBe(15);
    expect(booked.getMinutes()).toBe(30);
  });
});
