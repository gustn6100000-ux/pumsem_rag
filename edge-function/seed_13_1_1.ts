import { supabase } from "./config.ts";

const data = JSON.parse(Deno.readTextFileSync("../pipeline/scripts/records_13_1_1.json"));

async function seed() {
    console.log(`Starting insertion of ${data.length} records...`);

    // Batch upsert using Supabase
    const batchSize = 100;
    for (let i = 0; i < data.length; i += batchSize) {
        const batch = data.slice(i, i + batchSize);
        const { error } = await supabase.from("complex_table_specs").upsert(batch, {
            onConflict: "section_code, material, spec_mm, thickness_mm, pipe_location, joint_type, job_name"
        });

        if (error) {
            console.error(`Error inserting batch ${i / batchSize + 1}:`, error);
        } else {
            console.log(`Successfully inserted batch ${i / batchSize + 1}`);
        }
    }
    console.log("Seed complete.");
}

seed();
