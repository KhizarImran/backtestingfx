use std::fs::File;
use std::io::{BufRead, BufReader};
use crate::types::Bar;
use chrono::DateTime;

pub fn load_csv(path: &str) -> Vec<Bar> {
    let file = File::open(path).expect("Could not open file");
    let reader = BufReader::new(file);
    let mut bars = Vec::new();

    for line in reader.lines().skip(1) { //skips the header row
        let line = line.expect("could not read line");
        let cols: Vec<&str> = line.split(',').collect();

        let dt = DateTime::parse_from_rfc3339(cols[0]).expect("Bad Timestamp"); // this handles the timestamp to be in 2024-01-01 00:00:00 kind of way 

        let bar = Bar {
            timestamp: dt.timestamp(),
            open:      cols[1].parse().expect("Bad open"),
            high:      cols[2].parse().expect("Bad high"),
            low:       cols[3].parse().expect("Bad low"),
            close:     cols[4].parse().expect("Bad close"),
            volume:    cols[5].parse().expect("Bad volume"),
        };

        bars.push(bar); // pushes each bar from the csv to an element in Bar 

    }

    bars
}