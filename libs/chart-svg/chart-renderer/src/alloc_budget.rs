//! The engine's global allocator, able to refuse allocations past a budget.
//!
//! Unarmed, it is `System` behind one relaxed atomic load per call. The
//! forked child that renders a custom chart's template (`sandbox`) arms it:
//! there an allocation the budget refuses aborts the child alone, and the
//! parent reports the abort as the chart's error. The host process never
//! arms it, because Rust answers a refused allocation with `abort`.

use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering::Relaxed};

pub struct Budgeted;

static ARMED: AtomicBool = AtomicBool::new(false);
static CAP: AtomicUsize = AtomicUsize::new(usize::MAX);
static USED: AtomicUsize = AtomicUsize::new(0);

/// Count every allocation from here on against `cap` bytes.
pub fn arm(cap: usize) {
    USED.store(0, Relaxed);
    CAP.store(cap, Relaxed);
    ARMED.store(true, Relaxed);
}

fn charge(bytes: usize) -> bool {
    USED.fetch_add(bytes, Relaxed) + bytes <= CAP.load(Relaxed)
}

/// Memory the host allocated before arming is freed under the budget too,
/// so the count saturates at zero instead of wrapping.
fn release(bytes: usize) {
    let _ = USED.fetch_update(Relaxed, Relaxed, |used| Some(used.saturating_sub(bytes)));
}

unsafe impl GlobalAlloc for Budgeted {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        if ARMED.load(Relaxed) && !charge(layout.size()) {
            return std::ptr::null_mut();
        }
        System.alloc(layout)
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        if ARMED.load(Relaxed) && !charge(layout.size()) {
            return std::ptr::null_mut();
        }
        System.alloc_zeroed(layout)
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        if ARMED.load(Relaxed) {
            release(layout.size());
        }
        System.dealloc(ptr, layout)
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        if ARMED.load(Relaxed) {
            let old = layout.size();
            if new_size > old && !charge(new_size - old) {
                return std::ptr::null_mut();
            }
            if new_size < old {
                release(old - new_size);
            }
        }
        System.realloc(ptr, layout, new_size)
    }
}
