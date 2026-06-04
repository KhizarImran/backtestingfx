use std::fs::File;
use std::io::{BufRead, BufReader};
use crate::types::Bar;

pub fn load_csv(path: &str) -> Vec<Bar> {
    let file = File::open(path).expect("Could not open file");
    let reader = BufReader::new(file);
    let mut bars = Vec::new();

    for line in reader.lines().skip(1) { //skips the header row
        let line = line.expect("could not read line");
        let cols: Vec<&str> = line.split(',').collect();

        let bar = Bar {
            timestamp: cols[0].parse().expect("Bad timestamp"),
            open:      cols[1].parse().expect("Bad open"),
            high:      cols[2].parse().expect("Bad high"),
            low:       cols[3].parse().expect("Bad low"),
            close:     cols[4].parse().expect("Bad close"),
            volume:    cols[5].parse().expect("Bad volume"),
        };

        bars.push(bar);

    }

    bars
}