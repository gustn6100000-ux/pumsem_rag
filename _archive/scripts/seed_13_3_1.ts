import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'
import data from './records_13_3_1.json' with { type: 'json' }

const supabaseUrl = Deno.env.get('SUPABASE_URL')
const supabaseKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')

if (!supabaseUrl || !supabaseKey) {
    console.error("Missing SUPABASE credentials in .env")
    Deno.exit(1)
}

const supabase = createClient(supabaseUrl, supabaseKey)

async function seed() {
    console.log(`Starting to seed ${data.length} records...`)
    const chunkSize = 100

    for (let i = 0; i < data.length; i += chunkSize) {
        const chunk = data.slice(i, i + chunkSize)
        const { error } = await supabase
            .from('complex_table_specs')
            .upsert(chunk, { onConflict: 'section_code,material,spec_mm,thickness_mm,pipe_location,joint_type,job_name' })

        if (error) {
            console.error(`Error on batch ${Math.floor(i / chunkSize) + 1}:`, error.message)
        } else {
            console.log(`Batch ${Math.floor(i / chunkSize) + 1} Success.`)
        }
    }

    console.log('Finished seeding all records.')
}

seed()
