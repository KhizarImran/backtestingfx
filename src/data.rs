use crate::types::Bar;
use chrono::DateTime;
use csv::Reader;
use std::fs::File;

pub fn load_csv(path: &str) -> Vec<Bar> {
    let file = File::open(path).expect("Could not open file");
    let mut rdr = Reader::from_reader(file);
    let headers = rdr.headers().expect("could not read headers").clone();
    let col = |name: &str| headers.iter().position(|h| h == name).expect(name);
    let mut bars = Vec::new();

    for result in rdr.records() {
        let r = result.expect("could not read record");
        let dt = DateTime::parse_from_rfc3339(&r[0]).expect("Bad timestamp");

        bars.push(Bar {
            timestamp: dt.timestamp(),
            open: r[col("open")].parse().expect("Bad open"),
            high: r[col("high")].parse().expect("Bad high"),
            low: r[col("low")].parse().expect("Bad low"),
            close: r[col("close")].parse().expect("Bad close"),
            volume: r[col("volume")].parse().expect("Bad volume"),
        });
    }

    bars
}
