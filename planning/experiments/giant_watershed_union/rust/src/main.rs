use std::fs::File;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::time::Instant;

use byteorder::{LittleEndian, ReadBytesExt};
use geo::{Area, BooleanOps, BoundingRect, MultiPolygon, Polygon};
use geozero::ToGeo;
use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct SpatialKey {
    morton: u64,
    min_x: OrderedFloat,
    min_y: OrderedFloat,
    unsigned_area: OrderedFloat,
    first_x: OrderedFloat,
    first_y: OrderedFloat,
}

impl SpatialKey {
    fn empty() -> Self {
        Self {
            morton: 0,
            min_x: OrderedFloat::new(0.0),
            min_y: OrderedFloat::new(0.0),
            unsigned_area: OrderedFloat::new(0.0),
            first_x: OrderedFloat::new(0.0),
            first_y: OrderedFloat::new(0.0),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct OrderedFloat(u64);

impl OrderedFloat {
    fn new(value: f64) -> Self {
        let bits = value.to_bits();
        let key = if bits & (1_u64 << 63) == 0 {
            bits | (1_u64 << 63)
        } else {
            !bits
        };
        Self(key)
    }
}

fn morton_key(x: f64, y: f64) -> u64 {
    let xi = quantize_for_morton(x);
    let yi = quantize_for_morton(y);
    interleave_bits(xi) | (interleave_bits(yi) << 1)
}

fn quantize_for_morton(value: f64) -> u32 {
    let normalized = if value.is_finite() {
        ((value + 180.0) / 360.0).clamp(0.0, 1.0)
    } else {
        0.0
    };
    (normalized * f64::from(u16::MAX)).round() as u32
}

fn interleave_bits(mut value: u32) -> u64 {
    value &= 0x0000_ffff;
    let mut spread = u64::from(value);
    spread = (spread | (spread << 16)) & 0x0000_ffff_0000_ffff;
    spread = (spread | (spread << 8)) & 0x00ff_00ff_00ff_00ff;
    spread = (spread | (spread << 4)) & 0x0f0f_0f0f_0f0f_0f0f;
    spread = (spread | (spread << 2)) & 0x3333_3333_3333_3333;
    (spread | (spread << 1)) & 0x5555_5555_5555_5555
}

fn spatial_key(polygon: &Polygon<f64>) -> SpatialKey {
    let Some(bounds) = polygon.bounding_rect() else {
        return SpatialKey::empty();
    };

    let center_x = (bounds.min().x + bounds.max().x) * 0.5;
    let center_y = (bounds.min().y + bounds.max().y) * 0.5;
    let first_vertex = polygon.exterior().0.first();
    SpatialKey {
        morton: morton_key(center_x, center_y),
        min_x: OrderedFloat::new(bounds.min().x),
        min_y: OrderedFloat::new(bounds.min().y),
        unsigned_area: OrderedFloat::new(polygon.unsigned_area()),
        first_x: OrderedFloat::new(first_vertex.map_or(0.0, |coord| coord.x)),
        first_y: OrderedFloat::new(first_vertex.map_or(0.0, |coord| coord.y)),
    }
}

fn deterministic_reduce(mut polygons: Vec<MultiPolygon<f64>>) -> MultiPolygon<f64> {
    match polygons.len() {
        0 => MultiPolygon::new(vec![]),
        1 => MultiPolygon::new(vec![]).union(&polygons.remove(0)),
        len => {
            let right = polygons.split_off(len / 2);
            let (left, right) = rayon::join(
                || deterministic_reduce(polygons),
                || deterministic_reduce(right),
            );
            left.union(&right)
        }
    }
}

fn deterministic_reduce_serial(mut polygons: Vec<MultiPolygon<f64>>) -> MultiPolygon<f64> {
    match polygons.len() {
        0 => MultiPolygon::new(vec![]),
        1 => MultiPolygon::new(vec![]).union(&polygons.remove(0)),
        len => {
            let right = polygons.split_off(len / 2);
            let left = deterministic_reduce_serial(polygons);
            let right = deterministic_reduce_serial(right);
            left.union(&right)
        }
    }
}

#[cfg(test)]
fn dissolve_spatial_reduce_strategy(mut polygons: Vec<Polygon<f64>>) -> MultiPolygon<f64> {
    polygons.sort_by_key(spatial_key);
    deterministic_reduce(
        polygons
            .into_iter()
            .map(|p| MultiPolygon::new(vec![p]))
            .collect(),
    )
}

#[cfg(test)]
fn dissolve_spatial_reduce_serial(mut polygons: Vec<Polygon<f64>>) -> MultiPolygon<f64> {
    polygons.sort_by_key(spatial_key);
    deterministic_reduce_serial(
        polygons
            .into_iter()
            .map(|p| MultiPolygon::new(vec![p]))
            .collect(),
    )
}

#[cfg(test)]
fn dissolve_reduce_strategy(polygons: Vec<Polygon<f64>>) -> MultiPolygon<f64> {
    deterministic_reduce(
        polygons
            .into_iter()
            .map(|p| MultiPolygon::new(vec![p]))
            .collect(),
    )
}

fn get_peak_rss_mb() -> f64 {
    #[cfg(target_os = "linux")]
    {
        if let Ok(status) = std::fs::read_to_string("/proc/self/status") {
            for line in status.lines() {
                if line.starts_with("VmHWM:") {
                    let parts: Vec<&str> = line.split_whitespace().collect();
                    if parts.len() >= 2 {
                        if let Ok(kb) = parts[1].parse::<f64>() {
                            return kb / 1024.0;
                        }
                    }
                }
            }
        }
    }

    let mut rusage = std::mem::MaybeUninit::uninit();
    let ret = unsafe { libc::getrusage(libc::RUSAGE_SELF, rusage.as_mut_ptr()) };
    if ret == 0 {
        let r = unsafe { rusage.assume_init() };
        #[cfg(target_os = "macos")]
        return (r.ru_maxrss as f64) / (1024.0 * 1024.0);
        #[cfg(not(target_os = "macos"))]
        return (r.ru_maxrss as f64) / 1024.0;
    }
    0.0
}

#[derive(Serialize)]
struct BenchmarkResult {
    name: String,
    mode: String,
    threads: usize,
    read_decode_seconds: f64,
    preparation_seconds: f64,
    sorting_seconds: f64,
    kernel_seconds: f64,
    diagnostics_seconds: f64,
    write_seconds: f64,
    total_seconds: f64,
    timing_scope: &'static str,
    peak_rss_mb: f64,
    memory_scope: &'static str,
    input_record_count: usize,
    input_polygon_count: usize,
    polygon_count: usize,
    total_area: f64,
    bounds: Option<[f64; 4]>,
    is_valid: Option<bool>,
}

struct PolygonInputs {
    ids: Vec<u64>,
    polygons: Vec<Polygon<f64>>,
}

fn read_wkb_inputs(path: &PathBuf) -> Result<PolygonInputs, Box<dyn std::error::Error>> {
    let file = File::open(path)?;
    let mut reader = BufReader::new(file);
    let mut ids = Vec::new();
    let mut polygons = Vec::new();

    while !reader.fill_buf()?.is_empty() {
        let id = reader.read_u64::<LittleEndian>()?;
        let len = reader.read_u32::<LittleEndian>()? as usize;
        let mut buf = vec![0u8; len];
        reader.read_exact(&mut buf)?;

        let geom = geozero::wkb::Wkb(buf).to_geo()?;
        match geom {
            geo::Geometry::Polygon(p) => {
                polygons.push(p);
                ids.push(id);
            }
            geo::Geometry::MultiPolygon(mp) => {
                for p in mp.0 {
                    polygons.push(p);
                }
                ids.push(id);
            }
            _ => return Err(format!("Non-polygonal geometry for ID {id}").into()),
        }
    }
    Ok(PolygonInputs { ids, polygons })
}

fn write_wkb_multipolygon(
    path: &PathBuf,
    mp: &MultiPolygon<f64>,
) -> Result<(), Box<dyn std::error::Error>> {
    use geozero::ToWkb;
    let geom = geo::Geometry::MultiPolygon(mp.clone());
    let wkb_bytes = geom.to_wkb(geozero::CoordDimensions::xy())?;
    let mut f = File::create(path)?;
    f.write_all(&wkb_bytes)?;
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let total_start = Instant::now();
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 5 {
        return Err("Usage: giant-watershed-union-rust INPUT.wkb_bin OUTPUT_DIR MODE(spatial|serial|unsorted) THREADS".into());
    }
    let mode = args[3].as_str();
    let name = match mode {
        "spatial" => "rust_spatial_reduce_parallel",
        "serial" => "rust_spatial_reduce_serial",
        "unsorted" => "rust_unsorted_reduce_parallel",
        _ => return Err(format!("Unknown mode: {mode}").into()),
    };
    let threads = args[4].parse::<usize>()?;
    if threads == 0 || (mode == "serial" && threads != 1) {
        return Err("THREADS must be positive; serial requires THREADS=1".into());
    }
    let input_path = PathBuf::from(&args[1]);
    let output_dir = PathBuf::from(&args[2]);
    std::fs::create_dir_all(&output_dir)?;

    let read_start = Instant::now();
    let PolygonInputs { ids, mut polygons } = read_wkb_inputs(&input_path)?;
    let read_decode_seconds = read_start.elapsed().as_secs_f64();
    let input_record_count = ids.len();
    let input_polygon_count = polygons.len();

    let prepare_start = Instant::now();
    drop(ids);
    let pool = if mode == "serial" {
        None
    } else {
        Some(
            rayon::ThreadPoolBuilder::new()
                .num_threads(threads)
                .build()?,
        )
    };
    let mut preparation_seconds = prepare_start.elapsed().as_secs_f64();
    let sorting_start = Instant::now();
    if mode != "unsorted" {
        polygons.sort_by_key(spatial_key);
    }
    let sorting_seconds = sorting_start.elapsed().as_secs_f64();
    let prepare_start = Instant::now();
    let operands = polygons
        .into_iter()
        .map(|p| MultiPolygon::new(vec![p]))
        .collect();
    preparation_seconds += prepare_start.elapsed().as_secs_f64();

    let kernel_start = Instant::now();
    let result = match pool.as_ref() {
        Some(pool) => pool.install(|| deterministic_reduce(operands)),
        None => deterministic_reduce_serial(operands),
    };
    let kernel_seconds = kernel_start.elapsed().as_secs_f64();
    let diagnostics_start = Instant::now();
    let bounds = result
        .bounding_rect()
        .map(|b| [b.min().x, b.min().y, b.max().x, b.max().y]);
    let total_area = result.unsigned_area();
    let diagnostics_seconds = diagnostics_start.elapsed().as_secs_f64();
    let write_start = Instant::now();
    write_wkb_multipolygon(&output_dir.join(format!("{name}.wkb")), &result)?;
    let write_seconds = write_start.elapsed().as_secs_f64();
    let peak_rss_mb = get_peak_rss_mb();
    let total_seconds = total_start.elapsed().as_secs_f64();
    let record = BenchmarkResult {
        name: name.to_string(), mode: mode.to_string(), threads,
        read_decode_seconds, preparation_seconds, sorting_seconds, kernel_seconds,
        diagnostics_seconds, write_seconds, total_seconds,
        timing_scope: "main entry through WKB write and RSS sample; excludes JSON report, process startup and external validation",
        peak_rss_mb,
        memory_scope: "isolated process lifetime high-water RSS through WKB serialization/write, MiB",
        input_record_count, input_polygon_count, polygon_count: result.0.len(),
        total_area, bounds, is_valid: None,
    };
    let json = serde_json::to_vec_pretty(&record)?;
    std::fs::write(output_dir.join("rust_benchmark_results.json"), &json)?;
    println!("{}", String::from_utf8(json)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use geo::LineString;

    fn rect(x0: f64, y0: f64, x1: f64, y1: f64) -> Polygon<f64> {
        Polygon::new(
            LineString::from(vec![(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]),
            vec![],
        )
    }

    #[test]
    fn test_spatial_reduce_equivalence() {
        let polygons = vec![
            rect(0.0, 0.0, 1.0, 1.0),
            rect(1.0, 0.0, 2.0, 1.0),
            rect(0.0, 1.0, 1.0, 2.0),
            rect(1.0, 1.0, 2.0, 2.0),
        ];
        let p_par = dissolve_spatial_reduce_strategy(polygons.clone());
        let p_ser = dissolve_spatial_reduce_serial(polygons.clone());
        let p_uns = dissolve_reduce_strategy(polygons);

        assert_eq!(p_par.0.len(), 1);
        assert_eq!(p_ser.0.len(), 1);
        assert_eq!(p_uns.0.len(), 1);

        assert!((p_par.unsigned_area() - 4.0).abs() < 1e-6);
        assert!((p_ser.unsigned_area() - 4.0).abs() < 1e-6);
        assert!((p_uns.unsigned_area() - 4.0).abs() < 1e-6);
    }

    #[test]
    fn wkb_reader_preserves_holes_members_and_record_count() {
        use geozero::ToWkb;
        let outer = rect(0.0, 0.0, 4.0, 4.0);
        let hole = rect(1.0, 1.0, 2.0, 2.0);
        let holed = Polygon::new(outer.exterior().clone(), vec![hole.exterior().clone()]);
        let detached = rect(10.0, 10.0, 11.0, 11.0);
        let geometries = [
            geo::Geometry::Polygon(holed.clone()),
            geo::Geometry::MultiPolygon(MultiPolygon(vec![detached.clone(), holed.clone()])),
        ];
        let path = std::env::temp_dir().join(format!("union-reader-{}.bin", std::process::id()));
        let mut bytes = Vec::new();
        for (id, geometry) in geometries.iter().enumerate() {
            let wkb = geometry.to_wkb(geozero::CoordDimensions::xy()).unwrap();
            bytes.extend_from_slice(&(id as u64).to_le_bytes());
            bytes.extend_from_slice(&(wkb.len() as u32).to_le_bytes());
            bytes.extend_from_slice(&wkb);
        }
        std::fs::write(&path, &bytes).unwrap();
        let PolygonInputs { ids, polygons } = read_wkb_inputs(&path).unwrap();
        assert_eq!(polygons, vec![holed.clone(), detached.clone(), holed]);
        assert_eq!(ids, vec![0, 1]);
        // An incomplete ID, length, or payload is not a successful EOF.
        for length in [1, 9, bytes.len() - 1] {
            std::fs::write(&path, &bytes[..length]).unwrap();
            assert!(read_wkb_inputs(&path).is_err());
        }
        let point = geo::Geometry::Point(geo::Point::new(0.0, 0.0));
        let wkb = point.to_wkb(geozero::CoordDimensions::xy()).unwrap();
        let mut nonpolygon = 42_u64.to_le_bytes().to_vec();
        nonpolygon.extend_from_slice(&(wkb.len() as u32).to_le_bytes());
        nonpolygon.extend_from_slice(&wkb);
        std::fs::write(&path, nonpolygon).unwrap();
        assert!(read_wkb_inputs(&path).is_err());
        std::fs::remove_file(path).unwrap();
    }
}
