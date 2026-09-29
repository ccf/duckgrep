pub struct Circle { pub r: f64 }

impl Circle {
    pub fn new(r: f64) -> Self { Circle { r } }
}

pub fn area(c: &Circle) -> f64 { 3.14 * c.r * c.r }
