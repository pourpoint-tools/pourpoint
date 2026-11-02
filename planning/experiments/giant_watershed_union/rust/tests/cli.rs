use geo::{Area, Geometry};
use geozero::ToGeo;
use std::process::Command;

#[test]
fn isolated_candidates_preserve_a_hole_and_reject_all_mode() {
    let dir = std::env::temp_dir().join(format!("union-cli-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    // Independently encoded little-endian Polygon: 4x4 exterior, 1x1 hole.
    let mut wkb = vec![1];
    wkb.extend_from_slice(&3_u32.to_le_bytes());
    wkb.extend_from_slice(&2_u32.to_le_bytes());
    for ring in [
        [
            (0.0_f64, 0.0_f64),
            (4.0, 0.0),
            (4.0, 4.0),
            (0.0, 4.0),
            (0.0, 0.0),
        ],
        [(1.0, 1.0), (1.0, 2.0), (2.0, 2.0), (2.0, 1.0), (1.0, 1.0)],
    ] {
        wkb.extend_from_slice(&5_u32.to_le_bytes());
        for (x, y) in ring {
            wkb.extend_from_slice(&x.to_le_bytes());
            wkb.extend_from_slice(&y.to_le_bytes());
        }
    }
    let mut detached = vec![1];
    detached.extend_from_slice(&3_u32.to_le_bytes());
    detached.extend_from_slice(&1_u32.to_le_bytes());
    detached.extend_from_slice(&5_u32.to_le_bytes());
    for (x, y) in [
        (10.0_f64, 0.0_f64),
        (11.0, 0.0),
        (11.0, 1.0),
        (10.0, 1.0),
        (10.0, 0.0),
    ] {
        detached.extend_from_slice(&x.to_le_bytes());
        detached.extend_from_slice(&y.to_le_bytes());
    }
    let mut multipart = vec![1];
    multipart.extend_from_slice(&6_u32.to_le_bytes());
    multipart.extend_from_slice(&2_u32.to_le_bytes());
    multipart.extend(wkb);
    multipart.extend(detached);
    let wkb = multipart;
    let mut input = 1_u64.to_le_bytes().to_vec();
    input.extend_from_slice(&(wkb.len() as u32).to_le_bytes());
    input.extend(wkb);
    let path = dir.join("input.bin");
    std::fs::write(&path, input).unwrap();
    for mode in ["spatial", "serial", "unsorted"] {
        let output_dir = dir.join(mode);
        let output = Command::new(env!("CARGO_BIN_EXE_giant-watershed-union-rust"))
            .arg(&path)
            .arg(&output_dir)
            .args([mode, if mode == "serial" { "1" } else { "2" }])
            .output()
            .unwrap();
        assert!(output.status.success(), "{:?}", output);
        let report: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(report["input_record_count"], 1);
        assert_eq!(report["input_polygon_count"], 2);
        assert!(report["is_valid"].is_null());
        let total = report["total_seconds"].as_f64().unwrap();
        let stages: f64 = [
            "read_decode_seconds",
            "preparation_seconds",
            "sorting_seconds",
            "kernel_seconds",
            "diagnostics_seconds",
            "write_seconds",
        ]
        .iter()
        .map(|key| report[key].as_f64().unwrap())
        .sum();
        assert!(total >= stages);
        let bytes =
            std::fs::read(output_dir.join(format!("{}.wkb", report["name"].as_str().unwrap())))
                .unwrap();
        let Geometry::MultiPolygon(result) = geozero::wkb::Wkb(bytes).to_geo().unwrap() else {
            panic!("expected MultiPolygon")
        };
        assert_eq!(result.0.len(), 2);
        assert_eq!(
            result.0.iter().map(|p| p.interiors().len()).sum::<usize>(),
            1
        );
        assert_eq!(result.unsigned_area(), 16.0);
    }
    let output = Command::new(env!("CARGO_BIN_EXE_giant-watershed-union-rust"))
        .arg(&path)
        .arg(dir.join("invalid"))
        .args(["all", "1"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(!dir.join("invalid").exists());
    std::fs::remove_dir_all(dir).unwrap();
}
