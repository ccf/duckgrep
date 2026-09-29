mod shapes;
use crate::shapes::{Circle, area};

/// Entry point.
pub fn total() -> f64 {
    let c = Circle::new(1.0);
    area(&c)
}
